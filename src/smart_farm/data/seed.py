"""演示数据生成（开发用）。

生成一段时间范围内的模拟传感器数据，便于本地无真实设备时体验平台。
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


def seed(days: int = 30, interval_minutes: int = 30) -> None:
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
        gh_id = s.query(Greenhouse).first().id

        start = datetime.now() - timedelta(days=days)
        step = timedelta(minutes=interval_minutes)
        t = start
        while t <= datetime.now():
            hour = t.hour + t.minute / 60.0
            # 简单的日周期模拟
            temp = 22 + 6 * np_sin(hour)
            hum = 60 + 10 * np_sin(hour + 4)
            soil = 45 + 8 * np_sin(hour + 2)
            nutrient = 50 + 5 * np_sin(hour)
            light = max(0, 1200 + 1800 * np_sin(hour - 6)) if 6 <= hour <= 18 else 50
            s.add(AirTemperatureHumidity(greenhouse_id=gh_id, temperature=round(temp, 2), humidity=round(hum, 2), timestamp=t))
            s.add(SoilMoisture(greenhouse_id=gh_id, value=round(soil, 2), timestamp=t))
            s.add(SoilNutrient(greenhouse_id=gh_id, value=round(nutrient, 2), timestamp=t))
            s.add(LightIntensity(greenhouse_id=gh_id, value=round(light, 2), timestamp=t))
            t += step
    print(f"已生成约 {days} 天演示数据（间隔 {interval_minutes} 分钟）。")


def np_sin(hour: float) -> float:
    import math

    return math.sin((hour / 24.0) * 2 * math.pi)


if __name__ == "__main__":
    seed()
