"""jarvis.browser.readable: a page's main content without the site around it.

The two fixtures are modelled on real pages that went wrong: a Steam guide
(store menu, language list, award / favourite / share buttons, counters,
author block, comments, footer -- some of which came back as
"achievements") and a typical recipe page.
"""

from __future__ import annotations

from jarvis.browser.readable import extract_main_text

STEAM_GUIDE = """<html>
<head>
<title>Сообщество Steam :: Руководство :: Все достижения и как их получить (100%)</title>
</head>
<body class="responsive_page flat_page with_header">
<div class="responsive_page_frame with_header">
 <div id="global_header">
 <div class="content">
 <a href="#">Магазин</a>
 <a href="#">Главная</a>
 <a href="#">Рекомендации</a>
 <a href="#">Сообщество</a>
 <a href="#">Поддержка</a>
 </div>
 </div>
 <div id="language_pulldown" class="popup_block_new">
 <a>English (английский)</a>
 <a>Deutsch (немецкий)</a>
 <a>Polski (польский)</a>
 <a>Українська (украинский)</a>
 </div>
 <div class="responsive_page_template_content">
  <div class="apphub_HomeHeaderContent"><div class="apphub_AppName">BLEACH Rebirth of Souls</div>
    <div class="apphub_sectionTabs">
    <a>Все</a>
    <a>Обсуждения</a>
    <a>Скриншоты</a>
    <a>Руководства</a>
    <a>Обзоры</a>
    </div>
    </div>
  <div class="breadcrumbs">
  <a>BLEACH Rebirth of Souls</a> &gt; <a>Руководства</a> &gt; <a>Руководства Senk</a>
  </div>
  <div class="guideTop">
    <div class="workshopItemTitle">Все достижения и как их получить (100%)</div>
    <div class="ratingSection"><div class="numRatings">Недостаточно оценок</div></div>
    <div id="ItemControls" class="workshopItemControls">
      <span class="general_btn"><span>Наградить</span></span>
      <span class="general_btn">В избранное</span><span class="general_btn">Поделиться</span>
    </div>
    <div class="creatorsBlock">Создатели: Senk Не в сети</div>
    <div class="detailsStatsContainerRight">632 уникальных посетителей 25 добавили в избранное</div>
  </div>
  <div class="guide subSections">
    <div class="guideTopDescription">
    📌 В этом руководстве: все сюжетные и секретные достижения</div>
    <div class="subSection detailBox">
      <div class="subSectionTitle">Пройти обучение</div>
      <div class="subSectionDesc">и заходим в главном меню заходим в обучение и проходим его</div>
    </div>
    <div class="subSection detailBox">
      <div class="subSectionTitle">Крылья феникса</div>
      <div class="subSectionDesc">покупаем 10 видов в магазине Урахары</div>
    </div>
    <div class="subSection detailBox">
      <div class="subSectionTitle">Король душ</div>
      <div class="subSectionDesc">Пройти все «реальные моменты» основной сюжетной линии.
      Пройти все секретные миссии и секретные моменты.</div>
    </div>
  </div>
  <div class="commentthread_area">
  <div class="commentthread_header">Комментарии</div>
  <div class="commentthread_comment">Спасибо за гайд!</div>
  </div>
 </div>
 <div id="footer">
 <div class="footer_content">
 © Valve Corporation. Все права сохранены. Политика конфиденциальности</div>
 </div>
</div>
</body></html>
"""

RECIPE = """<html><head><title>Борщ украинский — рецепт | Еда.ру</title></head><body>
<header class="site-header"><a>Рецепты</a><a>Статьи</a><a>Войти</a></header>
<div class="share-buttons"><a>VK</a><a>Telegram</a></div>
<article class="recipe">
<h1>Борщ украинский</h1>
<p class="recipe-yield">Порций: 4</p>
<h2>Ингредиенты</h2>
<ul class="ingredients">
<li>
<label>
<input type="checkbox">Свёкла — 2 шт</label>
</li>
<li>Говядина на кости — 500 г</li>
<li>Капуста — 300 г</li>
<li>Соль — по вкусу</li>
</ul>
<h2>Приготовление</h2>
<ol>
<li>Сварите бульон из говядины, 1,5 часа.</li>
<li>Натрите свёклу и потушите.</li>
<li>Добавьте капусту и варите 10 минут.</li>
</ol>
</article>
<section class="comments"><h3>Комментарии (12)</h3><p>Очень вкусно!</p></section>
<footer>© Еда.ру</footer>
</body></html>
"""


class TestSteamGuide:
    def test_keeps_every_achievement_with_its_description(self):
        text = extract_main_text(STEAM_GUIDE)
        lines = text.splitlines()
        for name, desc in [
            ("Пройти обучение", "и заходим в главном меню"),
            ("Крылья феникса", "покупаем 10 видов в магазине Урахары"),
            ("Король душ", "Пройти все «реальные моменты»"),
        ]:
            i = lines.index(f"## {name}")
            assert lines[i + 1].startswith(desc)

    def test_drops_site_chrome(self):
        text = extract_main_text(STEAM_GUIDE)
        for junk in (
            "Магазин", "English (английский)", "Наградить", "В избранное", "Поделиться",
            "уникальных посетителей", "Создатели", "Недостаточно оценок", "Обсуждения",
            "Спасибо за гайд", "Valve Corporation", "Руководства Senk",
        ):
            assert junk not in text, junk


class TestRecipe:
    def test_ingredients_and_steps_are_marked(self):
        text = extract_main_text(RECIPE)
        assert "- Свёкла — 2 шт" in text  # inside <label><input>, kept
        assert "- Соль — по вкусу" in text
        assert "1. Сварите бульон из говядины, 1,5 часа." in text
        assert "3. Добавьте капусту и варите 10 минут." in text
        assert "## Ингредиенты" in text and "## Приготовление" in text

    def test_article_preferred_comments_and_menus_dropped(self):
        text = extract_main_text(RECIPE)
        for junk in ("Рецепты", "Войти", "Telegram", "Комментарии", "Очень вкусно", "© Еда.ру"):
            assert junk not in text, junk


class TestSafety:
    def test_wrapper_with_chrome_like_class_is_kept(self):
        body = "<p>" + "Важный текст гайда. " * 40 + "</p>"
        html = f'<html><body><div class="page with_header">{body}</div></body></html>'
        assert "Важный текст гайда." in extract_main_text(html)

    def test_class_words_are_whole_words(self):
        html = (
            '<html><body><div class="headerless-article">'
            + "<p>Содержимое " * 30
            + '</div><div class="site-header">Меню</div></body></html>'
        )
        text = extract_main_text(html)
        assert "Содержимое" in text
        assert "Меню" not in text

    def test_link_farm_without_class_is_dropped(self):
        links = "".join(f"<li><a href='/l{i}'>Язык {i}</a></li>" for i in range(30))
        content = "<p>" + "Описание достижения. " * 60 + "</p>"
        html = f"<html><body><ul>{links}</ul>{content}</body></html>"
        text = extract_main_text(html)
        assert "Язык 5" not in text
        assert "Описание достижения." in text

    def test_plain_page_unchanged_in_substance(self):
        html = "<html><body><p>Первый абзац.</p><p>Второй абзац.</p></body></html>"
        assert extract_main_text(html) == "Первый абзац.\nВторой абзац."

    def test_max_chars(self):
        html = "<html><body><p>" + "x" * 500 + "</p></body></html>"
        assert len(extract_main_text(html, max_chars=100)) == 100


def test_entry_that_is_mostly_one_link_is_kept():
    """Steam guide "Врата 4 зверей": description = a short line + one long
    link. Mostly link text, but a single link is content, not a link farm."""
    entries = "".join(
        f"<div class='subSectionTitle'>Достижение {i}</div>"
        f"<div class='subSectionDesc'>Описание достижения номер {i} подробно</div>"
        for i in range(10)
    )
    html = (
        "<html><body><div class='guide'>" + entries
        + "<div class='subSectionTitle'>Врата 4 зверей</div>"
        "<div class='subSectionDesc'>подробный гайд в этом руководстве <br><br>"
        "<a href='https://steamcommunity.com/sharedfiles/filedetails/?id=3458379900'>"
        "https://steamcommunity.com/sharedfiles/filedetails/?id=3458379900</a></div>"
        "</div></body></html>"
    )
    lines = extract_main_text(html).splitlines()
    i = lines.index("## Врата 4 зверей")
    assert lines[i + 1] == "подробный гайд в этом руководстве"


def test_beautifulsoup_is_a_base_dependency():
    """B-P2-14: bs4 was only in the [browser] extra, so CI ([dev]) and plain
    installs fell back to the regex path -- menus and footers kept -- and
    11 tests failed. It is a runtime dependency of "remember this page"."""
    import tomllib
    from pathlib import Path

    from packaging.requirements import Requirement

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    assert "beautifulsoup4" in {Requirement(d).name for d in deps}
