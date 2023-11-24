from string import digits, ascii_letters
import copy
import uuid
import random
import json
import asyncio
from gsuid_core.utils.api.mys.tools import generate_passport_ds, mys_version
from gsuid_core.utils.api.mys.api import OLD_URL
from gsuid_core.utils.api.mys import MysApi
from typing import Dict, Optional
from gsuid_core.utils.database.models import GsUser

QR_login_SCAN="https://api-sdk.mihoyo.com/hk4e_cn/combo/panda/qrcode/scan"
QR_login_CONFIRM="https://api-sdk.mihoyo.com/hk4e_cn/combo/panda/qrcode/confirm"
GET_GAME_TOKEN = f"{OLD_URL}/auth/api/getGameToken"

class _MysApi(MysApi):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

mys_api = _MysApi()


async def qrlogin_game(url,qid,bid = "onebot"):
    if "https://user.mihoyo.com/qr_code_in_game.html" not in url:
        return "链接不正确哦~"
    ticket = url.split("ticket=")[1].split("&")[0]
    app_id=url.split("app_id=")[1].split("&")[0]
    biz_key=url.split("biz_key=")[1].split("&")[0]
    data={"ticket": ticket, "app_id": app_id}
    sk = await GsUser.get_user_stoken_by_user_id(qid, bid)
    if sk is None:
        return "你还没有绑定过Stoken~\n请发送 [扫码登陆]"
    code,message=await login_in_game_by_qrcode(data, sk,biz_key)
    if code != 0:
        return message
    else:
        return "帮帮捏~"

async def get_game_token(sk):
    HEADER = copy.deepcopy(mys_api._HEADER)
    HEADER["Cookie"] = sk
    param = {
        "uid": HEADER["Cookie"].split("stuid=")[1].split(";")[0],
        "stoken": HEADER["Cookie"].split("stoken=")[1].split(";")[0]
    }
    data = await mys_api._mys_request(
        url=GET_GAME_TOKEN,
        method='GET',
        header=HEADER,
        params=param,
    )
    return HEADER["Cookie"].split("stuid=")[1].split(";")[0], data




async def login_in_game_by_qrcode(info: dict, sk,biz_key):
    aid, game_token = await get_game_token(sk)
    if isinstance(game_token, int):
        return -999,"Stoken已失效~\n请发送 [扫码登陆]"
    game_token = game_token["data"]["game_token"]
    qrscan=QR_login_SCAN.replace("hk4e_cn",biz_key)
    qrconfirm=QR_login_CONFIRM.replace("hk4e_cn",biz_key)
    HEADER = {
        'x-rpc-app_version': mys_version,
        'x-rpc-aigis': '',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'x-rpc-game_biz': 'bbs_cn',
        'x-rpc-sys_version': '11',
        'x-rpc-app_id': 'bll8iq97cem8',
        'x-rpc-client_type': '2',
        'User-Agent': 'okhttp/4.8.0',
    }
    user_data = await GsUser.base_select_data(stoken=sk)
    if user_data and user_data.fp:
        HEADER["x-rpc-device_fp"] = user_data.fp
        HEADER["x-rpc-device_id"] = user_data.device_id
        if user_data.device_info:
            device_info = user_data.device_info.split('/')
            HEADER['x-rpc-device_name'] = f"{device_info[0]} {device_info[1]}"
            HEADER['x-rpc-device_model'] = device_info[1]
        else:
            HEADER['x-rpc-device_name'] = 'GenshinUid_login_device_lulu'
            HEADER['x-rpc-device_model'] = 'GenshinUid_login_device_lulu'
    else:
        HEADER["x-rpc-device_fp"] = ''.join(random.choices(ascii_letters + digits, k=13))
        HEADER["x-rpc-device_id"] = uuid.uuid4().hex

    data = info
    data["device"]=HEADER['x-rpc-device_id']
    print(data)
    HEADER['DS'] = generate_passport_ds(b=data)
    HEADER["Cookie"] = sk
    info=await mys_api._mys_request(
        url=qrscan,
        method='POST',
        header=HEADER,
        data=data,
    )
    if isinstance(info,int):
        if info == -106:
            return info,"二维码过期啦"
        elif info == -107:
            return info,"二维码扫过啦"
        else:
            return info,f"出错码{info}"
    if not info["message"] == "OK":
        return info["retcode"],info["message"]
    data["payload"] = {
        "proto": "Account",
        "raw": json.dumps({
            "uid": str(aid),
            "token": game_token
        },indent=4,ensure_ascii=False)
    }
    print(data)
    HEADER['DS'] = generate_passport_ds(b=data)
    await asyncio.sleep(5)
    info=await mys_api._mys_request(
        url=qrconfirm,
        method='POST',
        header=HEADER,
        data=data,
    )
    print(info)
    return info["retcode"],info["message"]

