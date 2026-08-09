from string import digits, ascii_letters
import copy
import random
import re
import time
import uuid
import asyncio
from urllib.parse import parse_qs, unquote, urlparse

from gsuid_core.utils.api.mys.tools import generate_passport_ds, mys_version
from gsuid_core.utils.api.mys import MysApi
from gsuid_core.utils.database.models import GsUser


QR_LOGIN_SCAN = "https://{game}-sdk.mihoyo.com/{biz_key}/combo/panda/qrcode/scan"
PASSPORT_QR_SCAN = (
    "https://passport-api.mihoyo.com/account/ma-cn-passport/app/scanQRLogin"
)
PASSPORT_QR_CONFIRM = (
    "https://passport-api.mihoyo.com/account/ma-cn-passport/app/confirmQRLogin"
)
PASSPORT_APP_ID = "bll8iq97cem8"
DEFAULT_APP_IDS = {"hk4e_cn": 4, "nap_cn": 12}


class _MysApi(MysApi):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


mys_api = _MysApi()


def _parse_cookie(cookie: str) -> dict:
    result = {}
    for item in (cookie or "").split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _parse_qr_url(url: str) -> dict:
    parsed = urlparse(url)
    if "qr_code_in_game.html" not in parsed.path:
        raise ValueError("链接不正确哦~")
    query = parse_qs(parsed.query, keep_blank_values=True)

    def get(name):
        values = query.get(name)
        return unquote(values[0]) if values else ""

    biz_key = get("biz_key")
    if not re.fullmatch(r"[a-z0-9]+_[a-z0-9]+", biz_key):
        raise ValueError("二维码中的游戏标识不正确")
    ticket = get("ticket")
    if not ticket:
        raise ValueError("二维码中缺少 ticket")
    try:
        app_id = int(get("app_id"))
    except (TypeError, ValueError):
        app_id = DEFAULT_APP_IDS.get(biz_key)
    if app_id is None:
        raise ValueError("二维码中缺少游戏 app_id")
    return {
        "ticket": ticket,
        "app_id": app_id,
        "biz_key": biz_key,
        "app_name": get("app_name"),
    }


def _sdk_url(biz_key: str) -> str:
    if biz_key == "bh3_cn":
        game = "api"
    else:
        game = biz_key.split("_", 1)[0]
    return QR_LOGIN_SCAN.format(game=game, biz_key=biz_key)


def _response_error(response, default: str) -> str:
    if isinstance(response, int):
        return f"请求失败，错误码：{response}"
    if isinstance(response, dict) and response.get("message"):
        return str(response["message"])
    return default


async def _build_device_context(sk: str) -> dict:
    user_data = await GsUser.base_select_data(stoken=sk)
    if user_data and user_data.fp and user_data.device_id:
        device_id = user_data.device_id
        device_fp = user_data.fp
        device_name = ""
        device_model = ""
        if user_data.device_info:
            parts = user_data.device_info.split("/", 1)
            device_name = parts[0]
            device_model = parts[1] if len(parts) > 1 else parts[0]
        device_name = device_name or "MysQrLogin"
        device_model = device_model or device_name
    else:
        device_id = uuid.uuid4().hex
        device_fp = "".join(random.choices(ascii_letters + digits, k=13))
        device_name = device_model = "MysQrLogin"
    return {
        "device_id": device_id,
        "device_fp": device_fp,
        "device_name": device_name,
        "device_model": device_model,
        "lifecycle_id": str(uuid.uuid4()),
    }


def _build_common_headers(device: dict) -> dict:
    return {
        "Accept": "application/json",
        "x-rpc-app_id": PASSPORT_APP_ID,
        "x-rpc-client_type": "2",
        "x-rpc-device_id": device["device_id"],
        "x-rpc-device_fp": device["device_fp"],
        "x-rpc-device_name": device["device_name"],
        "x-rpc-device_model": device["device_model"],
        "x-rpc-sys_version": "11",
        "x-rpc-game_biz": "bbs_cn",
        "x-rpc-app_version": mys_version,
        "x-rpc-sdk_version": "2.42.0",
        "x-rpc-lifecycle_id": device["lifecycle_id"],
        "x-rpc-account_version": "2.42.0",
        "Content-Type": "application/json",
        "User-Agent": "okhttp/4.9.3",
    }


async def _scan_game_qr(qr_info: dict, device: dict):
    body = {
        "passport_app_id": PASSPORT_APP_ID,
        "ticket": qr_info["ticket"],
        "app_id": qr_info["app_id"],
        "device": device["device_id"],
        "ts": int(time.time()),
    }
    response = await mys_api._mys_request(
        url=_sdk_url(qr_info["biz_key"]),
        method="POST",
        header=_build_common_headers(device),
        data=body,
    )
    if isinstance(response, int) or not isinstance(response, dict):
        return None, _response_error(response, "获取二维码登录信息失败")
    if response.get("retcode") != 0:
        return None, _response_error(response, "获取二维码登录信息失败")
    qr_url = (response.get("data") or {}).get("passport_qr_url")
    if not qr_url:
        return None, "接口未返回官方二维码地址"
    parsed = urlparse(qr_url.replace(r"\u0026", "&"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    ticket_values = query.get("tk")
    if not ticket_values or not ticket_values[0]:
        return None, "官方二维码地址中缺少 tk"
    return {
        "ticket": unquote(ticket_values[0]),
        "token_types": [unquote(value) for value in (query.get("token_types") or ["1"])],
        "expire": (query.get("expire") or [""])[0],
    }, None


def _build_passport_headers(device: dict, cookie: dict, body: dict) -> dict:
    headers = _build_common_headers(device)
    headers["Cookie"] = f"stoken={cookie['stoken']};mid={cookie['mid']}"
    headers["DS"] = generate_passport_ds(b=body)
    return headers


async def _passport_qr_request(url: str, passport_info: dict, device: dict, cookie: dict):
    body = {
        "ticket": passport_info["ticket"],
        "token_types": passport_info["token_types"],
    }
    return await mys_api._mys_request(
        url=url,
        method="POST",
        header=_build_passport_headers(device, cookie, body),
        data=body,
    )


async def _official_qr_scan(passport_info: dict, device: dict, cookie: dict):
    response = await _passport_qr_request(PASSPORT_QR_SCAN, passport_info, device, cookie)
    if isinstance(response, int) or not isinstance(response, dict):
        return None, _response_error(response, "检查二维码状态失败")
    if response.get("retcode") != 0:
        return None, _response_error(response, "二维码尚未扫码或已失效")
    return response.get("data") or {}, None


async def _official_qr_confirm(passport_info: dict, device: dict, cookie: dict):
    response = await _passport_qr_request(PASSPORT_QR_CONFIRM, passport_info, device, cookie)
    if isinstance(response, int) or not isinstance(response, dict):
        return False, _response_error(response, "确认二维码登录失败")
    if response.get("retcode") != 0:
        return False, _response_error(response, "确认二维码登录失败")
    return True, ""


async def qrlogin_game(url, qid, bid="onebot"):
    try:
        qr_info = _parse_qr_url(url)
    except ValueError as exc:
        return str(exc)

    sk = await GsUser.get_user_stoken_by_user_id(qid, bid)
    if not sk:
        return "你还没有绑定过Stoken~\n请发送 [扫码登陆]"
    cookie = _parse_cookie(sk)
    if not cookie.get("stoken") or not cookie.get("mid"):
        return "Stoken中缺少 stoken 或 mid，请重新绑定Stoken~"

    device = await _build_device_context(sk)
    passport_info, error = await _scan_game_qr(qr_info, device)
    if error:
        return error
    scan_data, error = await _official_qr_scan(passport_info, device, cookie)
    if error:
        return error

    await asyncio.sleep(5)
    confirmed, error = await _official_qr_confirm(passport_info, device, cookie)
    if not confirmed:
        return error
    return "帮帮捏~"


async def login_in_game_by_qrcode(info: dict, sk, biz_key=None):
    """Compatibility wrapper for callers of the old implementation."""
    try:
        qr_info = {
            "ticket": info["ticket"],
            "app_id": int(info["app_id"]),
            "biz_key": biz_key or info.get("biz_key", ""),
            "app_name": info.get("app_name", ""),
        }
        cookie = _parse_cookie(sk)
        if not qr_info["biz_key"] or not cookie.get("stoken") or not cookie.get("mid"):
            return -1, "二维码参数或 Stoken 不完整"
        device = await _build_device_context(sk)
        passport_info, error = await _scan_game_qr(qr_info, device)
        if error:
            return -1, error
        _, error = await _official_qr_scan(passport_info, device, cookie)
        if error:
            return -1, error
        await asyncio.sleep(5)
        confirmed, error = await _official_qr_confirm(passport_info, device, cookie)
        return (0, "OK") if confirmed else (-1, error)
    except (KeyError, TypeError, ValueError) as exc:
        return -1, f"二维码参数不正确：{exc}"



