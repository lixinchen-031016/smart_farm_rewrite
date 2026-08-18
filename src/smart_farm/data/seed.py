"""演示数据生成（开发用，幂等）。

生成多个大棚 + 每棚一段时间的模拟传感器数据，便于本地无真实设备时体验平台
（多棚多用户、多租户隔离）。
时间戳对齐到间隔边界，重复运行只补增量，不产生重复记录。
用法： `python -m smart_farm.data.seed`
"""

import math
from datetime import datetime, timedelta

from smart_farm.data.database import get_session, init_db
from smart_farm.data.models import (
    AirTemperatureHumidity,
    Device,
    Greenhouse,
    LightIntensity,
    SoilMoisture,
    SoilNutrient,
    User,
    UserGreenhouse,
)
from smart_farm.services.auth_service import hash_password


def _sin(hour: float) -> float:
    return math.sin((hour / 24.0) * 2 * math.pi)


def _existing_timestamps(session, model, greenhouse_id: int, since: datetime) -> set:
    """拉取某表自 since 起已有的时间戳集合（幂等去重依据）。"""
    rows = (
        session.query(model.timestamp)
        .filter(model.greenhouse_id == greenhouse_id, model.timestamp >= since)
        .all()
    )
    return {r[0] for r in rows}


def _ensure_greenhouses(session, count: int) -> list[Greenhouse]:
    """幂等创建演示大棚（存在即复用）。"""
    names = [("一号智慧大棚", "示范园区东区"), ("二号智慧大棚", "示范园区西区"), ("三号育苗棚", "示范基地北侧")]
    result = []
    for name, loc in names[:count]:
        gh = session.query(Greenhouse).filter_by(name=name).first()
        if gh is None:
            gh = Greenhouse(name=name, location=loc)
            session.add(gh)
            session.flush()
        result.append(gh)
    return result


def seed(days: int = 30, interval_minutes: int = 30, greenhouse_count: int = 3) -> int:
    """幂等生成演示数据：多大棚/多用户/演示设备 + 每棚传感器数据增量补齐。

    Returns:
        本次新增的传感器记录行数（幂等：数据已完整时为 0）。
    """
    init_db()
    with get_session() as s:
        greenhouses = _ensure_greenhouses(s, greenhouse_count)

        # 管理员 + 园丁演示账号（存在即复用）；admin 授权全部棚，gardener 授权前两棚
        admin = s.query(User).filter_by(username="admin").first()
        if admin is None:
            admin = User(
                username="admin",
                password=hash_password("Admin@123456"),
                last_login_time=datetime.now(),
                role="admin",
            )
            s.add(admin)
            s.flush()
        gardener = s.query(User).filter_by(username="gardener").first()
        if gardener is None:
            gardener = User(
                username="gardener",
                password=hash_password("Gardener@123"),
                last_login_time=datetime.now(),
                role="user",
            )
            s.add(gardener)
            s.flush()

        existing_links = {
            (link.user_id, link.greenhouse_id)
            for link in s.query(UserGreenhouse).all()
        }
        for gh in greenhouses:
            for user in (admin, gardener):
                if user.role == "user" and gh.id not in {g.id for g in greenhouses[:2]}:
                    continue  # gardener 只授权前两棚
                if (user.id, gh.id) not in existing_links:
                    s.add(UserGreenhouse(user_id=user.id, greenhouse_id=gh.id, granted_at=datetime.now()))

        # 每棚一个演示设备（固定 key 便于文档演示；存在即复用）
        for gh, protocol in zip(greenhouses, ("http", "mqtt", "udp")):
            if not s.query(Device).filter_by(name=f"{gh.name}-演示节点").first():
                s.add(
                    Device(
                        device_key=f"sf-demo-{'a1b2' if protocol == 'http' else 'c3d4' if protocol == 'mqtt' else 'e5f6'}"
                        f"-{gh.id:02d}-0000000000",
                        name=f"{gh.name}-演示节点",
                        protocol=protocol,
                        greenhouse_id=gh.id,
                        enabled=True,
                        note="seed 演示设备（固定密钥，仅本地开发）",
                        created_at=datetime.now(),
                    )
                )

        now = datetime.now()
        # 以固定锚点对齐到间隔网格：多次运行生成的时间戳网格完全一致，set 去重才可靠
        epoch = datetime(2020, 1, 1)
        grid_minutes = int(
            (now - epoch).total_seconds() // 60 // interval_minutes * interval_minutes
        )
        end = epoch + timedelta(minutes=grid_minutes)
        start = end - timedelta(days=days)
        step = timedelta(minutes=interval_minutes)

        added = 0
        for idx, gh in enumerate(greenhouses):
            existing = {
                "air": _existing_timestamps(s, AirTemperatureHumidity, gh.id, start),
                "soil": _existing_timestamps(s, SoilMoisture, gh.id, start),
                "nutrient": _existing_timestamps(s, SoilNutrient, gh.id, start),
                "light": _existing_timestamps(s, LightIntensity, gh.id, start),
            }
            # 棚间基线错开，验证多棚隔离
            offset = idx * 1.8
            t = start
            while t <= end:
                hour = t.hour + t.minute / 60.0
                temp = 22 + 6 * _sin(hour) + offset
                hum = 60 + 10 * _sin(hour + 4) - offset
                soil = 45 + 8 * _sin(hour + 2) + offset
                nutrient = 50 + 5 * _sin(hour)
                light = 1200 + 1800 * _sin(hour - 6) if 6 <= hour <= 18 else 50
                if t not in existing["air"]:
                    s.add(
                        AirTemperatureHumidity(
                            greenhouse_id=gh.id,
                            temperature=round(temp, 2),
                            humidity=round(hum, 2),
                            timestamp=t,
                        )
                    )
                    added += 1
                if t not in existing["soil"]:
                    s.add(SoilMoisture(greenhouse_id=gh.id, value=round(soil, 2), timestamp=t))
                    added += 1
                if t not in existing["nutrient"]:
                    s.add(SoilNutrient(greenhouse_id=gh.id, value=round(nutrient, 2), timestamp=t))
                    added += 1
                if t not in existing["light"]:
                    s.add(LightIntensity(greenhouse_id=gh.id, value=round(max(light, 0), 2), timestamp=t))
                    added += 1
                t += step

    if added:
        print(f"已增量生成 {added} 行演示数据（{len(greenhouses)} 棚 × {days} 天 / 间隔 {interval_minutes} 分钟）。")
    else:
        print("演示数据已是最新，无需生成。")
    return added


if __name__ == "__main__":
    seed()
