"""Get hourly NLDAS2 forcing data."""
from __future__ import annotations

import functools
import itertools
import re
from io import BytesIO, StringIO
from typing import TYPE_CHECKING, Union

import async_retriever as ar
import pandas as pd
import pygeoutils as hgu
import pyproj
import rioxarray  # noqa: F401
import xarray as xr
from numpy.core._exceptions import UFuncTypeError
from pandas.errors import EmptyDataError

from .exceptions import InputRangeError, InputTypeError, InputValueError, NLDASServiceError

if TYPE_CHECKING:
    from shapely.geometry import MultiPolygon, Polygon

CRSTYPE = Union[int, str, pyproj.CRS]
URL = "https://hydro1.gesdisc.eosdis.nasa.gov/daac-bin/access/timeseries.cgi"
__all__ = ["get_bycoords", "get_bygeom"]

NLDAS_VARS = {
    "prcp": {"nldas_name": "APCPsfc", "long_name": "Precipitation hourly total", "units": "mm"},
    "pet": {"nldas_name": "PEVAPsfc", "long_name": "Potential evaporation", "units": "mm"},
    "temp": {"nldas_name": "TMP2m", "long_name": "2-m above ground temperature", "units": "K"},
    "wind_u": {
        "nldas_name": "UGRD10m",
        "long_name": "10-m above ground zonal wind",
        "units": "m/s",
    },
    "wind_v": {
        "nldas_name": "VGRD10m",
        "long_name": "10-m above ground meridional wind",
        "units": "m/s",
    },
    "rlds": {
        "nldas_name": "DLWRFsfc",
        "long_name": "Surface DW longwave radiation flux",
        "units": "W/m^2",
    },
    "rsds": {
        "nldas_name": "DSWRFsfc",
        "long_name": "Surface DW shortwave radiation flux",
        "units": "W/m^2",
    },
    "humidity": {
        "nldas_name": "SPFH2m",
        "long_name": "2-m above ground specific humidity",
        "units": "kg/kg",
    },
}


def get_byloc(
    lon: float,
    lat: float,
    start_date: str,
    end_date: str,
    variables: str | list[str] | None = None,
) -> pd.DataFrame:
    """Get NLDAS climate forcing data for a single location.

    Parameters
    ----------
    lon : float
        Longitude of the location.
    lat : float
        Latitude of the location.
    start_date : str
        Start date of the data.
    end_date : str
        End date of the data.
    variables : str or list of str, optional
        Variables to download. If None, all variables are downloaded.
        Valid variables are: ``prcp``, ``pet``, ``temp``, ``wind_u``, ``wind_v``,
        ``rlds``, ``rsds``, and ``humidity``.

    Returns
    -------
    pandas.DataFrame
        The requested data as a dataframe.
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date) + pd.Timedelta("1D")
    if start < pd.to_datetime("1979-01-01T13"):
        raise InputRangeError("start_date", "1979-01-01 to yesterday")
    if end > pd.Timestamp.now() - pd.Timedelta("1D"):
        raise InputRangeError("end_date", "1979-01-01 to yesterday")

    if variables is None:
        clm_vars = [f"NLDAS:NLDAS_FORA0125_H.002:{d['nldas_name']}" for d in NLDAS_VARS.values()]
    else:
        clm_vars = list(variables) if isinstance(variables, list) else [variables]
        if any(v not in NLDAS_VARS for v in variables):
            raise InputValueError("variables", list(NLDAS_VARS))
        clm_vars = [f"NLDAS:NLDAS_FORA0125_H.002:{NLDAS_VARS[v]['nldas_name']}" for v in variables]

    dates = pd.date_range(start, end, freq="10000D").tolist()
    dates = dates + [end] if dates[-1] < end else dates
    kwds = [
        {
            "params": {
                "type": "asc2",
                "location": f"GEOM:POINT({lon}, {lat})",
                "variable": v,
                "startDate": s.strftime("%Y-%m-%dT%H"),
                "endDate": e.strftime("%Y-%m-%dT%H"),
            }
        }
        for (s, e), v in itertools.product(zip(dates[:-1], dates[1:]), clm_vars)
    ]

    resp = ar.retrieve_text([URL] * len(kwds), kwds, max_workers=4)

    def txt2df(txt: str, resp_id: int) -> pd.Series:
        try:
            data = pd.read_csv(StringIO(txt), skiprows=39, delim_whitespace=True).dropna()
        except EmptyDataError:
            return pd.Series(name=kwds[resp_id]["params"]["variable"].split(":")[-1])
        except UFuncTypeError as ex:
            msg = "".join(re.findall("<strong>(.*?)</strong>", txt, re.DOTALL)).strip()
            raise NLDASServiceError(msg) from ex
        data.index = pd.to_datetime(data.index + " " + data["Date&Time"])
        data = data.drop(columns="Date&Time")
        data.index.freq = data.index.inferred_freq
        data = data["Data"]
        data.name = kwds[resp_id]["params"]["variable"].split(":")[-1]
        return data

    clm_list = (txt2df(txt, i) for i, txt in enumerate(resp))
    clm_merged = (
        pd.concat(df)
        for _, df in itertools.groupby(
            sorted(clm_list, key=lambda x: x.name), lambda x: x.name  # type: ignore[no-any-return]
        )
    )
    clm = pd.concat(clm_merged, axis=1)
    clm = clm.rename(columns={d["nldas_name"]: n for n, d in NLDAS_VARS.items()})
    return clm.loc[start_date:end_date]  # type: ignore[misc]


def get_bycoords(
    coords: list[tuple[float, float]],
    start_date: str,
    end_date: str,
    variables: str | list[str] | None = None,
    to_xarray: bool = False,
) -> pd.DataFrame:
    """Get NLDAS climate forcing data for a list of coordinates.

    Parameters
    ----------
    coords : list of tuples
        List of (lon, lat) coordinates.
    start_date : str
        Start date of the data.
    end_date : str
        End date of the data.
    variables : str or list of str, optional
        Variables to download. If None, all variables are downloaded.
        Valid variables are: ``prcp``, ``pet``, ``temp``, ``wind_u``, ``wind_v``,
        ``rlds``, ``rsds``, and ``humidity``.
    to_xarray : bool, optional
        If True, the data is returned as an xarray dataset.

    Returns
    -------
    pandas.DataFrame
        The requested data as a dataframe.
    """
    if not isinstance(coords, list) or any(len(c) != 2 for c in coords):
        raise InputTypeError("coords", "list of tuples of length 2", "[(lon, lat), ...]")

    lons, lats = zip(*coords)
    bounds = (-125.0, 25.0, -67.0, 53.0)
    pts = hgu.Coordinates(lons, lats, bounds)
    coords_val = list(zip(pts.points.x, pts.points.y))
    if len(coords_val) != len(coords):
        raise InputRangeError("coords", f"{bounds}")

    nldas = functools.partial(
        get_byloc, variables=variables, start_date=start_date, end_date=end_date
    )
    clm = pd.concat(
        (nldas(lon=lon, lat=lat) for lon, lat in coords_val),
        keys=coords_val,
    )
    clm.index = clm.index.set_names(["lon", "lat", "time"])
    if to_xarray:
        clm = clm.reset_index()
        clm["time"] = clm["time"].dt.tz_localize(None)
        clm_ds = clm.set_index(["lon", "lat", "time"]).to_xarray()
        clm_ds.attrs["tz"] = "UTC"
        clm_ds = clm_ds.rio.write_crs(4326)
        for v in clm_ds.data_vars:
            clm_ds[v].attrs = NLDAS_VARS[v]
        return clm_ds


def get_bygeom(
    geometry: Polygon | MultiPolygon,
    start_date: str,
    end_date: str,
    geo_crs: CRSTYPE,
    variables: str | list[str] | None = None,
) -> xr.Dataset:
    """Get hourly NLDAS climate forcing within a geometry at 0.125 resolution.

    Parameters
    ----------
    geometry : shapely.Polygon, shapely.MultiPolygon
        Input geometry.
    start_date : str
        Start date of the data.
    end_date : str
        End date of the data.
    geo_crs : int, str, or pyproj.CRS
        CRS of the input geometry
    variables : str or list of str, optional
        Variables to download. If None, all variables are downloaded.
        Valid variables are: ``prcp``, ``pet``, ``temp``, ``wind_u``, ``wind_v``,
        ``rlds``, ``rsds``, and ``humidity``.

    Returns
    -------
    xarray.Dataset
        The requested forcing data.
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date) + pd.Timedelta("1D")
    if start < pd.to_datetime("1979-01-01T13"):
        raise InputRangeError("start_date", "1979-01-01 to yesterday")
    if end > pd.Timestamp.now() - pd.Timedelta("1D"):
        raise InputRangeError("end_date", "1979-01-01 to yesterday")

    if variables is None:
        clm_vars = [f"NLDAS:NLDAS_FORA0125_H.002:{d['nldas_name']}" for d in NLDAS_VARS.values()]
    else:
        clm_vars = list(variables) if isinstance(variables, list) else [variables]
        if any(v not in NLDAS_VARS for v in variables):
            raise InputValueError("variables", list(NLDAS_VARS))
        clm_vars = [f"NLDAS:NLDAS_FORA0125_H.002:{NLDAS_VARS[v]['nldas_name']}" for v in variables]

    dates = pd.date_range(start, end, freq="10000D").tolist()
    dates = dates + [end] if dates[-1] < end else dates

    url = "/".join(
        (
            "https://ldas.gsfc.nasa.gov/sites/default",
            "files/ldas/nldas/NLDAS_masks-veg-soil.nc4",
        )
    )
    resp = ar.retrieve_binary([url])
    nldas_grid = xr.open_dataset(BytesIO(resp[0]))
    nldas_grid = nldas_grid.rio.write_crs(4326)
    geom = hgu.geo2polygon(geometry, geo_crs, nldas_grid.rio.crs)
    msk = nldas_grid.CONUS_mask.rio.clip([geom], all_touched=True)
    coords = itertools.product(msk.get_index("lon"), msk.get_index("lat"))
    kwds = [
        {
            "params": {
                "type": "asc2",
                "location": f"GEOM:POINT({lon}, {lat})",
                "variable": v,
                "startDate": s.strftime("%Y-%m-%dT%H"),
                "endDate": e.strftime("%Y-%m-%dT%H"),
            }
        }
        for (lon, lat), (s, e), v in itertools.product(coords, zip(dates[:-1], dates[1:]), clm_vars)
    ]
    resp = ar.retrieve_text([URL] * len(kwds), kwds, max_workers=4)

    def txt2da(txt: str, resp_id: int) -> xr.DataArray:
        try:
            data = pd.read_csv(StringIO(txt), skiprows=39, delim_whitespace=True).dropna()
        except EmptyDataError:
            return xr.DataArray(name=kwds[resp_id]["params"]["variable"].split(":")[-1])
        except UFuncTypeError as ex:
            msg = "".join(re.findall("<strong>(.*?)</strong>", txt, re.DOTALL)).strip()
            raise NLDASServiceError(msg) from ex
        data.index = pd.to_datetime(data.index + " " + data["Date&Time"])
        data = data["Data"]
        data.name = kwds[resp_id]["params"]["variable"].split(":")[-1]
        data.index.name = "time"
        data.index = data.index.tz_localize(None)
        da = data.to_xarray()
        lon, lat = kwds[resp_id]["params"]["location"].split("(")[-1].strip(")").split(",")
        da = da.assign_coords(x=float(lon), y=float(lat))
        da = da.expand_dims("y").expand_dims("x")
        return da

    clm = xr.merge(txt2da(txt, i) for i, txt in enumerate(resp))
    clm = clm.rename({d["nldas_name"]: n for n, d in NLDAS_VARS.items() if d["nldas_name"] in clm})
    clm = clm.sel(time=slice(start_date, end_date))
    clm.attrs["tz"] = "UTC"
    clm = clm.transpose("time", "y", "x")
    for v in clm:
        clm[v].attrs = NLDAS_VARS[str(v)]
    clm = clm.rio.write_transform()
    clm = clm.rio.write_crs(4326)
    clm = clm.rio.write_coordinate_system()
    return clm