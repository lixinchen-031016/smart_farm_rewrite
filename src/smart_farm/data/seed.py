"""演示数据生成（开发用，幂等）。

生成一段时间范围内的模拟传感器数据，便于本地无真实设备时体验平台。
时间戳对齐到间隔边界，重复运行只补增量，不产生重复记录。
用法： `python -m smart_farm.data.seed`
"""

from datetime import datetime, timedelta

from smart_farm.data.database import get_session, init_db
from smart_farm.data.models import (
    AirTemperatureHumidity,
    Greenhouse,
    LightIntensity,
    SoilMoisture,
    SoilNutrient,
    User,
)
from smart_farm.services.auth_service import hash_password


def _np_sin(hour: float) -> float:
    import math

    return math.sin((hour / 24.0) * 2 * math.pi)


def _existing_timestamps(session, model, greenhouse_id: int, since: datetime) -> set:
    """拉取某表自 since 起已有的时间戳集合（幂等去重依据）。"""
    rows = (
        session.query(model.timestamp)
        .filter(model.greenhouse_id == greenhouse_id, model.timestamp >= since)
        .all()
    )
    return {r[0] for r in rows}


def seed(days: int = 30, interval_minutes: int = 30) -> int:
    """幂等生成演示数据：大棚/管理员存在即复用，传感器数据按时间戳去重增量补齐。

    Returns:
        本次新增的传感器记录行数（幂等：数据已完整时为 0）。
    """
    init_db()
    with get_session() as s:
        # 确保存在一个大棚 + 一个管理员账户
        if not s.query(Greenhouse).first():
            s.add(Greenhouse(name="一号智慧大棚", location="示范园区"))
        if not s.query(User).filter_by(username="admin").first():
            s.add(
                User(
                    username="admin",
                    password=hash_password("Admin@123456"),
                    last_login_time=datetime.now(),
                    role="admin",
                )
            )
        s.flush()
        gh_id = s.query(Greenhouse).order_by(Greenhouse.id).first().id

        now = datetime.now()
        # 以固定锚点对齐到间隔网格：多次运行生成的时间戳网格完全一致，set 去重才可靠
        epoch = datetime(2020, 1, 1)
        grid_minutes = int(
            (now - epoch).total_seconds() // 60 // interval_minutes * interval_minutes
        )
        end = epoch + timedelta(minutes=grid_minutes)
        start = end - timedelta(days=days)
        step = timedelta(minutes=interval_minutes)

        existing = {
            "air": _existing_timestamps(s, AirTemperatureHumidity, gh_id, start),
            "soil": _existing_timestamps(s, SoilMoisture, gh_id, start),
            "nutrient": _existing_timestamps(s, SoilNutrient, gh_id, start),
            "light": _existing_timestamps(s, LightIntensity, gh_id, start),
        }

        added = 0
        t = start
        while t <= end:
            hour = t.hour + t.minute / 60.0
            # 简单的日周期模拟
            temp = 22 + 6 * _np_sin(hour)
            hum = 60 + 10 * _np_sin(hour + 4)
            soil = 45 + 8 * _np_sin(hour + 2)
            nutrient = 50 + 5 * _np_sin(hour)
            light = max(0, 1200 + 1800 * _np_sin(hour - 6)) if 6 <= hour <= 18 else 50
            if t not in existing["air"]:
                s.add(
                    AirTemperatureHumidity(
                        greenhouse_id=gh_id,
                        temperature=round(temp, 2),
                        humidity=round(hum, 2),
                        timestamp=t,
                    )
                )
                added += 1
            if t not in existing["soil"]:
                s.add(SoilMoisture(greenhouse_id=gh_id, value=round(soil, 2), timestamp=t))
                added += 1
            if t not in existing["nutrient"]:
                s.add(SoilNutrient(greenhouse_id=gh_id, value=round(nutrient, 2), timestamp=t))
                added += 1
            if t not in existing["light"]:
                s.add(LightIntensity(greenhouse_id=gh_id, value=round(light, 2), timestamp=t))
                added += 1
            t += step

    if added:
        print(f"已增量生成 {added} 行演示数据（{days} 天 / 间隔 {interval_minutes} 分钟，已存在数据跳过）。")
    else:
        print("演示数据已是最新，无需生成。")
    return added


if __name__ == "__main__":
    seed()
