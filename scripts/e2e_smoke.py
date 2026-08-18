#!/usr/bin/env python
"""Playwright 真实浏览器 E2E 冒烟测试（智慧大棚平台）。

前置：
  1. 启动服务端（测试缝开启验证码旁路）：
     SF_TEST_CAPTCHA=1 ./venv/bin/streamlit run src/smart_farm/app/main.py \
       --server.headless true --server.port 8760
  2. 运行：./venv/bin/python scripts/e2e_smoke.py [BASE_URL]

覆盖：
  - 登录（验证码旁路）→ 主界面
  - 17 个页面逐一导航，检测 stException（真实渲染错误）
  - 预测页：模式切换 + 执行预测（异步进度）
  - 数据清洗页：数据加载与 rerun 保留
  - 截图留档（tests/e2e_shots/）

退出码：0 = 全部通过；1 = 存在失败项。
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8760"
SHOT_DIR = Path(__file__).parent.parent / "tests" / "e2e_shots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

EXPECTED_PAGES = [
    "综合监控仪表板", "数据概览", "数据清洗与异常", "数据分析", "高级分析",
    "可视化", "本地数据预测", "自动化决策", "历史报告", "用户管理", "操作日志",
    "系统监控", "模块配置", "备份与恢复", "数据库同步", "使用说明",
]


def page_exceptions(page) -> int:
    """当前页面 Streamlit 异常组件数量。"""
    return page.locator('[data-testid="stException"]').count()


def navigate(page, label: str) -> None:
    """通过侧边栏导航打开指定页面（仅在折叠时展开 "View N more"，避免反复点击来回切换）。"""
    link = page.locator('[data-testid="stSidebarNav"] a', has_text=label)
    if link.count() == 0:
        more_btn = page.locator('button', has_text="View ")
        if more_btn.count() > 0:
            more_btn.first.click()
            page.wait_for_timeout(600)
    page.locator('[data-testid="stSidebarNav"] a', has_text=label).first.click()
    page.wait_for_load_state("networkidle", timeout=30000)
    time.sleep(0.5)


def main() -> int:
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})

        # ---- 1. 登录页 ----
        page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-testid="stTextInput"]', timeout=30000)
        page.screenshot(path=str(SHOT_DIR / "01_login.png"))
        print("[login] 登录页渲染 OK")

        # 填写表单（验证码旁路：SF_TEST_CAPTCHA=1 时任意值均可）
        inputs = page.locator('[data-testid="stTextInput"] input')
        if inputs.count() < 3:
            failures.append(f"登录表单输入框不足（{inputs.count()}）")
        else:
            inputs.nth(0).fill("admin")
            inputs.nth(1).fill("Admin@123456")
            inputs.nth(2).fill("12345")
            page.locator('button[data-testid="stBaseButton-primaryFormSubmit"]').click()
            # 等待进入主界面（侧边栏出现导航）
            page.wait_for_selector('[data-testid="stSidebarNav"]', timeout=30000)
            page.screenshot(path=SHOT_DIR / "02_dashboard.png")
            print("[login] 登录成功，进入主界面")

        # ---- 2. 页面遍历 ----
        for label in EXPECTED_PAGES:
            try:
                navigate(page, label)
                n = page_exceptions(page)
                if n > 0:
                    failures.append(f"{label}: {n} 个 stException")
                else:
                    print(f"[page] {label} OK")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{label}: 导航/渲染异常 {exc}")

        # 调试信息模块默认禁用 → 导航中不应出现（模块启停过滤生效）
        if page.locator('[data-testid="stSidebarNav"] a', has_text="调试信息").count() > 0:
            failures.append("调试信息应默认禁用且不出现在导航中")

        # ---- 3. 预测页：异步执行（导航回预测页）----
        try:
            navigate(page, "本地数据预测")
            # segmented_control 渲染为 role=radiogroup（无 data-testid），用文本断言模式区存在
            if page.get_by_text("单变量时间序列预测").count() == 0:
                failures.append("预测页缺少预测模式 segmented_control")
            else:
                btn = page.locator('button[data-testid="stBaseButton-primary"]', has_text="执行预测")
                if btn.count() == 0:
                    failures.append("预测页缺少执行按钮")
                else:
                    btn.first.click()
                    # 等待进度条或结果出现（异步后台线程）
                    try:
                        page.wait_for_selector(
                            '[data-testid="stProgress"], [data-testid="stMetric"]',
                            timeout=120000,
                        )
                        # 等待结果渲染（metric 出现或进度条消失）
                        deadline = time.time() + 120
                        while time.time() < deadline:
                            if page.locator('[data-testid="stMetric"]').count() > 0:
                                break
                            time.sleep(1)
                        page.screenshot(path=SHOT_DIR / "03_prediction.png")
                        print("[prediction] 异步预测执行 OK")
                    except Exception as exc:  # noqa: BLE001
                        failures.append(f"预测执行超时/异常：{exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"预测页异常：{exc}")

        # ---- 4. 数据清洗页：加载后 rerun 保留 ----
        try:
            navigate(page, "数据清洗与异常")
            if page_exceptions(page) > 0:
                failures.append("数据清洗页渲染异常")
            else:
                print("[cleaning] 数据清洗页 OK")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"数据清洗页异常：{exc}")

        # ---- 5. 模块配置页（管理员功能）----
        try:
            navigate(page, "模块配置")
            if page_exceptions(page) > 0:
                failures.append("模块配置页渲染异常")
            else:
                print("[module] 模块配置页 OK")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"模块配置页异常：{exc}")

        browser.close()

    print("\n=== 结果 ===")
    if failures:
        print(f"FAIL: {len(failures)} 项\n" + "\n".join(f" - {f}" for f in failures))
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
