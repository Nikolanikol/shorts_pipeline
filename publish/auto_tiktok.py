"""
Авто-публикация в TikTok через браузерную автоматизацию (Selenium).

Требования:
    pip install selenium webdriver-manager

Использование:
    python -m publish.auto_tiktok --video-id abc123 --movie "Форсаж 9" --genre action
    python -m publish.auto_tiktok --video-id abc123 --limit 2   # опубликовать 2 видео
"""

import argparse
import json
import time
from pathlib import Path

from loguru import logger

from config.settings import settings
from publish.manual import prepare_manual_publish, HASHTAGS


def _get_driver():
    """Создаёт Chrome WebDriver с антидетект-настройками."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()
    # Отключаем признаки автоматизации
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Скрываем webdriver через JS
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def _login(driver, email: str, password: str) -> bool:
    """Логинится в TikTok. Возвращает True если успешно."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logger.info("Открываем TikTok...")
    driver.get("https://www.tiktok.com/login/phone-or-email/email")
    time.sleep(3)

    try:
        wait = WebDriverWait(driver, 15)

        email_field = wait.until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        email_field.clear()
        email_field.send_keys(email)
        time.sleep(0.5)

        password_field = driver.find_element(By.XPATH, "//input[@type='password']")
        password_field.clear()
        password_field.send_keys(password)
        time.sleep(0.5)

        login_btn = driver.find_element(
            By.XPATH, "//button[@data-e2e='login-button']"
        )
        login_btn.click()
        time.sleep(5)

        # Проверяем что залогинились (ищем иконку профиля)
        wait.until(
            EC.presence_of_element_located((By.XPATH, "//div[@data-e2e='nav-profile']"))
        )
        logger.info("Авторизация успешна")
        return True

    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
        logger.warning("Если появилась капча — реши её вручную в течение 30 секунд")
        time.sleep(30)
        return False


def _upload_video(driver, video_path: str, caption: str) -> bool:
    """Загружает одно видео в TikTok. Возвращает True если успешно."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    try:
        wait = WebDriverWait(driver, 30)

        logger.info(f"Загружаем: {Path(video_path).name}")
        driver.get("https://www.tiktok.com/upload")
        time.sleep(4)

        # Загружаем файл
        file_input = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        file_input.send_keys(str(Path(video_path).resolve()))
        logger.info("  Файл отправлен, ждём обработки...")
        time.sleep(10)

        # Вводим описание
        caption_field = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[@contenteditable='true']")
            )
        )
        caption_field.click()
        time.sleep(0.5)

        # Очищаем и вводим текст
        from selenium.webdriver.common.keys import Keys
        caption_field.send_keys(Keys.CONTROL + "a")
        caption_field.send_keys(Keys.DELETE)
        time.sleep(0.3)

        # Вводим по частям (хэштеги отдельно)
        lines = caption.split("\n")
        for line in lines:
            caption_field.send_keys(line)
            caption_field.send_keys(Keys.RETURN)
            time.sleep(0.2)

        time.sleep(2)

        # Нажимаем "Опубликовать"
        post_btn = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(@class, 'btn-post')]")
            )
        )
        post_btn.click()
        logger.info("  Кнопка 'Опубликовать' нажата")
        time.sleep(8)

        logger.info(f"  ✓ Видео опубликовано: {Path(video_path).name}")
        return True

    except Exception as e:
        logger.error(f"Ошибка загрузки видео: {e}")
        return False


def auto_publish(
    video_id: str,
    movie_name: str = "",
    genre: str = "default",
    platform: str = "tiktok",
    limit: int = None,
    delay_between: int = 300,
) -> int:
    """
    Автоматически публикует видео в TikTok.

    Args:
        video_id: ID видео
        movie_name: название фильма
        genre: жанр для хэштегов
        platform: платформа
        limit: максимум видео за сессию (None = все)
        delay_between: пауза между публикациями в секундах

    Returns:
        количество успешно опубликованных видео
    """
    email = settings.tiktok_account_email
    password = settings.tiktok_account_password

    if not email or not password:
        logger.error(
            "Не заданы TIKTOK_ACCOUNT_EMAIL и TIKTOK_ACCOUNT_PASSWORD в .env"
        )
        return 0

    # Подготавливаем материалы
    publish_dir = prepare_manual_publish(video_id, movie_name, genre, platform)

    manifest_path = publish_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if limit:
        manifest = manifest[:limit]

    driver = _get_driver()
    published = 0

    try:
        if not _login(driver, email, password):
            logger.error("Не удалось войти в TikTok")
            return 0

        for item in manifest:
            video_path = publish_dir / item["video"]
            caption_file = publish_dir / item["caption_file"]
            caption = caption_file.read_text(encoding="utf-8")

            success = _upload_video(driver, str(video_path), caption)
            if success:
                published += 1

            if published < len(manifest):
                logger.info(f"Пауза {delay_between}с перед следующим постом...")
                time.sleep(delay_between)

    finally:
        driver.quit()

    logger.info(f"\n✅ Опубликовано: {published}/{len(manifest)} видео")
    return published


def main():
    parser = argparse.ArgumentParser(description="Авто-публикация видео в TikTok")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--movie", default="")
    parser.add_argument("--genre", default="default", choices=list(HASHTAGS.keys()))
    parser.add_argument("--platform", default="tiktok")
    parser.add_argument("--limit", type=int, default=None, help="Макс. кол-во публикаций")
    parser.add_argument(
        "--delay", type=int, default=300,
        help="Пауза между публикациями в секундах (по умолчанию 5 мин)"
    )
    args = parser.parse_args()

    auto_publish(
        video_id=args.video_id,
        movie_name=args.movie,
        genre=args.genre,
        platform=args.platform,
        limit=args.limit,
        delay_between=args.delay,
    )


if __name__ == "__main__":
    main()
