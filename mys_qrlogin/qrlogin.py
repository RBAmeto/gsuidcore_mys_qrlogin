
from string import digits, ascii_letters
import copy
import uuid
import random
import json
import asyncio
from gsuid_core.gss import gss
from gsuid_core.utils.api.mys.tools import generate_passport_ds
from gsuid_core.utils.api.mys.api import OLD_URL
from gsuid_core.utils.api.mys import MysApi
from gsuid_core.utils.error_reply import UID_HINT
from typing import Dict, Optional
from sqlalchemy import event
from gsuid_core.data_store import get_res_path
from gsuid_core.utils.database.dal import SQLA

QR_login_SCAN="https://api-sdk.mihoyo.com/hk4e_cn/combo/panda/qrcode/scan"
QR_login_CONFIRM="https://api-sdk.mihoyo.com/hk4e_cn/combo/panda/qrcode/confirm"
GET_GAME_TOKEN = f"{OLD_URL}/auth/api/getGameToken"

class _MysApi(MysApi):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

mys_api = _MysApi()
is_wal = False

active_sqla: Dict[str, SQLA] = {}
db_url = str(get_res_path().parent / 'GsData.db')

def get_sqla(bot_id) -> SQLA:
    if bot_id not in active_sqla:
        sqla = SQLA(db_url, bot_id)
        active_sqla[bot_id] = sqla
        sqla.create_all()

        @event.listens_for(sqla.engine.sync_engine, 'connect')
        def engine_connect(conn, branch):
            if is_wal:
                cursor = conn.cursor()
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.close()

    return active_sqla[bot_id]

async def qrlogin_game(url,qid,bid = "onebot"):
    if "https://user.mihoyo.com/qr_code_in_game.html" not in url:
        return "链接不正确哦~"
    ticket = url.split("ticket=")[1].split("&")[0]
    app_id=url.split("app_id=")[1].split("&")[0]
    biz_key=url.split("biz_key=")[1].split("&")[0]
    data={"ticket": ticket, "app_id": app_id}
    sqla = get_sqla(bid)
    uid = await sqla.get_bind_uid(qid)
    if uid is None:
        return UID_HINT
    code,message=await login_in_game_by_qrcode(data, uid,biz_key)
    if code != 0:
        return message
    else:
        return "帮帮捏~"

async def get_game_token(uid):
    HEADER = copy.deepcopy(mys_api._HEADER)
    HEADER["Cookie"] = await get_stoken(uid)
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
    return HEADER["Cookie"].split("stuid=")[1].split(";")[0], data["data"]["game_token"]

async def get_stoken(uid: str) -> Optional[str]:
        sqla = get_sqla('TEMP')
        return await sqla.get_user_stoken(uid)


async def login_in_game_by_qrcode(info: dict, uid,biz_key):
    aid, game_token = await get_game_token(uid)
    qrscan=QR_login_SCAN.replace("hk4e_cn",biz_key)
    qrconfirm=QR_login_CONFIRM.replace("hk4e_cn",biz_key)
    HEADER = {
        'x-rpc-app_version': '2.41.0',
        'x-rpc-aigis': '',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'x-rpc-game_biz': 'bbs_cn',
        'x-rpc-sys_version': '11',
        'x-rpc-device_id': uuid.uuid4().hex,
        'x-rpc-device_fp': ''.join(
            random.choices(ascii_letters + digits, k=13)
        ),
        'x-rpc-device_name': 'GenshinUid_login_device_lulu',
        'x-rpc-device_model': 'GenshinUid_login_device_lulu',
        'x-rpc-app_id': 'bll8iq97cem8',
        'x-rpc-client_type': '2',
        'User-Agent': 'okhttp/4.8.0',
    }
    data = info
    data["device"]=HEADER['x-rpc-device_id']
    print(data)
    HEADER['DS'] = generate_passport_ds(b=data)
    HEADER["Cookie"] = await get_stoken(uid)
    info=await mys_api._mys_request(
        url=qrscan,
        method='POST',
        header=HEADER,
        data=data,
    )
    print(info)
    if info["message"] != "OK":
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

