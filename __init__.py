from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from .qrlogin import qrlogin_game
from httpx import AsyncClient

sv_qrlogin = SV("米哈游游戏扫码登陆")
@sv_qrlogin.on_fullmatch(("帮帮捏","邦邦捏"))
async def one_more_thing(bot: Bot, ev: Event):
    qid = ev.user_id
    bid = ev.bot_id
    from io import BytesIO
    import cv2
    import numpy as np
    url = ev.image
    if not url:
        await bot.send("没有检测到图片捏")
        if len(str(ev.text).split("https://")) > 1:
            await bot.send("但是检测到链接捏")
            url="https://"+str(ev.text).split("https://")[1]
            msg = await qrlogin_game(url, qid, bid)
            await bot.send(msg)
            return 0
        else:
            # await bot.send("也没有检测到链接捏")
            return 0
    d=cv2.wechat_qrcode_WeChatQRCode()
    async with AsyncClient() as sess:
        resp = await sess.get(url)
        image=BytesIO(resp.content)
        image=cv2.imdecode(np.frombuffer(image.read(),np.uint8),cv2.IMREAD_COLOR)
        res, _ = d.detectAndDecode(image)
        for i in res:
            if "https" in i:
                url = i
        if "https" not in url:
            print(url)
            await bot.send("没有找到二维码捏")
            return 0
        print(url)
        msg = await qrlogin_game(url, qid, bid)
        await bot.send(msg)
        return 0
