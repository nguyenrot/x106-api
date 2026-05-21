"""Seed CMS rows for the redesigned `me` site (kynguyen.cc/me).

Every user-facing string carries `{en, vi}`. Non-translatable values stay as
plain strings. The migration is idempotent: it upserts each `(app, section)`
row via `update_or_create`.

Reverse migration deletes only the 7 rows it owns.
"""

from __future__ import annotations

from django.db import migrations


ME_SECTIONS: dict[str, dict] = {
    # -----------------------------------------------------------------
    # marquee — top ticker. English-only (per design).
    # -----------------------------------------------------------------
    "marquee": {
        "items": [
            {"text": "LIVE FROM ĐÀ NẴNG · 16.05°N 108.20°E", "dot": True},
            {"text": "CURRENTLY: BUILDING THINGS AT WORKDAY"},
            {"text": "STATUS: AVAILABLE FOR INTERESTING PROBLEMS"},
            {"text": "FUELED BY CÀ PHÊ SỮA ĐÁ & LOFI"},
            {"text": "SHIPPING SINCE 2021 · 1,200+ COMMITS THIS YEAR"},
        ]
    },
    # -----------------------------------------------------------------
    # hero
    # -----------------------------------------------------------------
    "hero": {
        "year": "2026",
        "status_badge": {
            "en": "Available · Đà Nẵng, VN",
            "vi": "Sẵn sàng · Đà Nẵng, VN",
        },
        "status_meta": {
            "en": "SDE @ Workday · EST. 2021",
            "vi": "SDE @ Workday · TỪ 2021",
        },
        "kicker_prefix": "// who_",
        "kicker": {"en": "introducing", "vi": "xin chào"},
        "name_first": "Phạm",
        "name_last": "Kỷ Nguyên",
        "role_label": "role",
        "role_value": "Software Development Engineer",
        "at_label": "at",
        "at_value": "Workday Việt Nam",
        "lede": {
            "en": (
                "I build backend systems and developer tools. Previously 4 years "
                "at Paradox, including a stint in Scottsdale, Arizona. Currently "
                "based in Đà Nẵng, Việt Nam."
            ),
            "vi": (
                "Tôi xây dựng hệ thống backend và công cụ cho developer. Trước đó "
                "gần 4 năm tại Paradox, gồm hai tháng on-site ở Scottsdale, "
                "Arizona. Hiện đang sống và làm việc tại Đà Nẵng, Việt Nam."
            ),
        },
        "cta_primary": {"en": "See projects", "vi": "Xem dự án"},
        "cta_ghost": {"en": "About me", "vi": "Về tôi"},
        "portrait": {
            "subject_label": {"en": "SUBJECT", "vi": "ĐỐI TƯỢNG"},
            "subject_value": "PHẠM KỶ NGUYÊN",
            "captured_label": {"en": "CAPTURED", "vi": "GHI HÌNH"},
            "captured_value": "2026 · ĐÀ NẴNG",
        },
        "datasheet": [
            {"label": "LAT", "value": "16.0544° N"},
            {"label": "LNG", "value": "108.2022° E"},
            {"label": "TZ", "value": "UTC+7 · ICT"},
            {
                "label": "STATUS",
                "value": {"en": "Open to collab", "vi": "Sẵn sàng hợp tác"},
                "ok": True,
            },
        ],
        "foot": {
            "en": "↳ scroll for the long version",
            "vi": "↳ kéo xuống để xem chi tiết",
        },
        "foot_dim": "page 01 / 06",
    },
    # -----------------------------------------------------------------
    # about
    # -----------------------------------------------------------------
    "about": {
        "section_num": {"en": "01 / about", "vi": "01 / giới thiệu"},
        "title_html": {
            "en": "Building reliable systems,<br/><span class=\"muted\">one merge at a time.</span>",
            "vi": "Xây hệ thống đáng tin cậy,<br/><span class=\"muted\">từng commit một.</span>",
        },
        "paragraphs_html": [
            {
                "en": (
                    "Born and raised in Đà Nẵng, Việt Nam. Started my career as "
                    "an Engineer Intern at <strong>Paradox</strong> in late 2021, "
                    "learning Python and Django from the ground up."
                ),
                "vi": (
                    "Sinh ra và lớn lên ở Đà Nẵng, Việt Nam. Bắt đầu sự nghiệp "
                    "với vị trí Engineer Intern tại <strong>Paradox</strong> "
                    "cuối năm 2021, học Python và Django từ con số 0."
                ),
            },
            {
                "en": (
                    "After nearly four years at Paradox — including a two-month "
                    "assignment in Scottsdale, Arizona — Paradox was acquired by "
                    "<strong>Workday</strong> in 2026, and I'm now part of the "
                    "Workday team as a Software Development Engineer."
                ),
                "vi": (
                    "Sau gần bốn năm tại Paradox — gồm hai tháng on-site ở "
                    "Scottsdale, Arizona — Paradox được <strong>Workday</strong> "
                    "mua lại vào năm 2026, và tôi hiện là thành viên của Workday "
                    "với vai trò Software Development Engineer."
                ),
            },
        ],
        "quote_html": {
            "en": "Make it correct. Then make it fast.<br/>Then leave it better than you found it.",
            "vi": "Làm cho đúng đã. Rồi làm cho nhanh.<br/>Rồi để lại tốt hơn lúc bạn nhận.",
        },
        "sig_prefix": {"en": "committed @ ", "vi": "commit @ "},
        "sig_year": "2026",
        "card_label": "$ whoami --verbose",
        "card_meta": [
            {"key": {"en": "Name", "vi": "Tên"}, "value": "Phạm Kỷ Nguyên"},
            {"key": {"en": "Location", "vi": "Vị trí"}, "value": "Đà Nẵng, Việt Nam"},
            {
                "key": {"en": "Role", "vi": "Vai trò"},
                "value": {"en": "Software Dev. Engineer", "vi": "Kỹ sư phần mềm"},
            },
            {"key": {"en": "Employer", "vi": "Công ty"}, "value": "Workday Việt Nam"},
            {"key": {"en": "Born", "vi": "Sinh"}, "value": "2000/ Đà Nẵng"},
            {
                "key": {"en": "Languages", "vi": "Ngôn ngữ"},
                "value": "Tiếng Việt · English",
            },
            {"key": {"en": "Coffee", "vi": "Cà phê"}, "value": "Cà phê sữa đá"},
        ],
    },
    # -----------------------------------------------------------------
    # skills — 8 disciplines
    # -----------------------------------------------------------------
    "skills": {
        "section_num": {"en": "02 / skills", "vi": "02 / kỹ năng"},
        "title": {
            "en": "What I work with daily.",
            "vi": "Những thứ tôi dùng hàng ngày.",
        },
        "aside_label": {"en": "disciplines", "vi": "lĩnh vực"},
        "aside_count": 8,
        "items": [
            {
                "idx": "S/01",
                "name": "Python",
                "pct": 92,
                "desc": {
                    "en": "Backbone of my work — used daily for backend services, scripts, and tooling.",
                    "vi": "Xương sống công việc — dùng hàng ngày cho backend, script và tooling.",
                },
            },
            {
                "idx": "S/02",
                "name": "Django",
                "pct": 88,
                "desc": {
                    "en": "Production web framework. Ship APIs, admin interfaces, and dashboards with it.",
                    "vi": "Framework web sản xuất. Ship API, trang admin và dashboard.",
                },
            },
            {
                "idx": "S/03",
                "name": "API Development",
                "pct": 90,
                "desc": {
                    "en": "Designing REST and GraphQL APIs. Versioning, authentication, pagination, observability.",
                    "vi": "Thiết kế REST và GraphQL API. Versioning, xác thực, phân trang, observability.",
                },
            },
            {
                "idx": "S/04",
                "name": "Integration",
                "pct": 85,
                "desc": {
                    "en": "Connecting third-party systems — payment, identity, analytics. Webhooks, retries, idempotency.",
                    "vi": "Kết nối hệ thống bên thứ ba — thanh toán, identity, analytics. Webhook, retry, idempotency.",
                },
            },
            {
                "idx": "S/05",
                "name": "Algorithmic Thinking",
                "pct": 83,
                "desc": {
                    "en": "Profiling, optimizing, and reasoning about correctness under load.",
                    "vi": "Profile, tối ưu và reasoning về tính đúng đắn dưới tải cao.",
                },
            },
            {
                "idx": "S/06",
                "name": "AI Agents",
                "pct": 78,
                "desc": {
                    "en": "Building agentic systems with LLMs. Tool use, structured outputs, evaluation harnesses.",
                    "vi": "Xây hệ thống agentic với LLM. Tool use, structured output, evaluation harness.",
                },
            },
            {
                "idx": "S/07",
                "name": "Database Software",
                "pct": 87,
                "desc": {
                    "en": "PostgreSQL, MySQL, Redis. Schema design, indexing, query tuning.",
                    "vi": "PostgreSQL, MySQL, Redis. Thiết kế schema, indexing, tối ưu query.",
                },
            },
            {
                "idx": "S/08",
                "name": {"en": "Code Testing", "vi": "Kiểm thử mã nguồn"},
                "pct": 86,
                "desc": {
                    "en": "Unit, integration, and end-to-end tests. Pytest, mocks, fixtures, CI gating, coverage.",
                    "vi": "Unit, integration và end-to-end test. Pytest, mocks, fixtures, gating CI, coverage.",
                },
            },
        ],
    },
    # -----------------------------------------------------------------
    # experience — 5 chapters
    # -----------------------------------------------------------------
    "experience": {
        "section_num": {"en": "03 / experience", "vi": "03 / kinh nghiệm"},
        "title": {"en": "Where I've been.", "vi": "Nơi tôi đã đi qua."},
        "aside_label": {"en": "chapters", "vi": "chương"},
        "aside_count": 5,
        "entries": [
            {
                "variant": "current",
                "year_start": "2026",
                "year_end": {"en": "Now", "vi": "Hiện tại"},
                "title": {
                    "en": "Software Development Engineer",
                    "vi": "Kỹ sư phát triển phần mềm",
                },
                "tag": {"en": "CURRENT", "vi": "HIỆN TẠI"},
                "org": "Workday",
                "location": "Remote / Đà Nẵng",
                "desc": {
                    "en": "Workday — one of the largest HCM platforms — acquired Paradox in 2026. I'm now part of the Workday team, continuing to build on what we shipped before.",
                    "vi": "Workday — một trong những nền tảng HCM lớn nhất — đã mua lại Paradox vào năm 2026. Tôi hiện là thành viên của Workday, tiếp tục xây dựng những gì chúng tôi đã ship trước đó.",
                },
            },
            {
                "year_start": "2022",
                "year_end": "2026",
                "title": {"en": "Software Engineer", "vi": "Kỹ sư phần mềm"},
                "org": "Paradox",
                "location": "Đà Nẵng",
                "desc": {
                    "en": "Nearly 4 years building HR automation tooling in the Đà Nẵng office. Grew from junior to senior — owning Django services, SQL pipelines, and integration work.",
                    "vi": "Gần 4 năm xây tooling HR automation tại văn phòng Đà Nẵng. Từ junior lên senior — own Django service, SQL pipeline và integration.",
                },
            },
            {
                "year_start": "2022",
                "year_end": None,
                "title": {
                    "en": "Software Engineer · Onsite",
                    "vi": "Kỹ sư phần mềm · Onsite",
                },
                "org": "Paradox",
                "location": "Scottsdale, Arizona 🇺🇸",
                "desc": {
                    "en": "Two-month on-site assignment in Scottsdale, Arizona. Worked closely with the US team and broadened my engineering toolbox.",
                    "vi": "Hai tháng on-site tại Scottsdale, Arizona. Làm việc chặt với team US và mở rộng toolbox kỹ thuật.",
                },
            },
            {
                "year_start": "2021",
                "year_end": "2022",
                "title": {"en": "Engineer Intern", "vi": "Thực tập sinh kỹ sư"},
                "org": "Paradox",
                "location": "Đà Nẵng",
                "desc": {
                    "en": "First role. Five months on-site in Đà Nẵng. Worked on the Vue.js front-end and learned the codebase.",
                    "vi": "Vị trí đầu tiên. Năm tháng on-site tại Đà Nẵng. Làm front-end Vue.js và học codebase.",
                },
            },
            {
                "variant": "edu",
                "year_start": "2018",
                "year_end": "2022",
                "title": {
                    "en": "B.Sc., Computer Software Engineering",
                    "vi": "Cử nhân Kỹ thuật Phần mềm Máy tính",
                },
                "org": "Duy Tan University",
                "location": "Đà Nẵng",
                "desc": {
                    "en": "Four years studying Computer Software Engineering. Graduated with 'Good' classification.",
                    "vi": "Bốn năm học Kỹ thuật Phần mềm Máy tính. Tốt nghiệp loại 'Giỏi'.",
                },
            },
        ],
    },
    # -----------------------------------------------------------------
    # projects — 6 cards
    # -----------------------------------------------------------------
    "projects": {
        "section_num": {"en": "04 / projects", "vi": "04 / dự án"},
        "title": {"en": "Side projects.", "vi": "Dự án cá nhân."},
        "aside_label": {"en": "live", "vi": "đang chạy"},
        "aside_count": 6,
        "items": [
            {
                "idx": "P/01",
                "name": "Vibe Hub",
                "desc": {
                    "en": "Personal site hub and landing page.",
                    "vi": "Trang chủ và landing cá nhân.",
                },
                "url": "https://kynguyen.cc",
                "url_label": "kynguyen.cc",
                "stack": "next · ts",
                "tone": "lime",
            },
            {
                "idx": "P/02",
                "name": "Daily Vibe Journal",
                "desc": {
                    "en": "Daily mood tracker — Server Actions + JWT cookies.",
                    "vi": "Theo dõi tâm trạng hàng ngày — Server Actions + JWT cookies.",
                },
                "url": "https://journal.kynguyen.cc",
                "url_label": "journal.kynguyen.cc",
                "stack": "next · auth",
                "tone": "cyan",
            },
            {
                "idx": "P/03",
                "name": "Generative Art",
                "desc": {
                    "en": "p5.js canvas sketches, persisted to localStorage.",
                    "vi": "Sketch p5.js trên canvas, lưu vào localStorage.",
                },
                "url": "https://art.kynguyen.cc",
                "url_label": "art.kynguyen.cc",
                "stack": "p5 · canvas",
                "tone": "magenta",
            },
            {
                "idx": "P/04",
                "name": "Neon Quotes",
                "desc": {
                    "en": "Quote collection with a clean reader UI.",
                    "vi": "Bộ sưu tập trích dẫn với reader UI sạch.",
                },
                "url": "https://quotes.kynguyen.cc",
                "url_label": "quotes.kynguyen.cc",
                "stack": "next · mdx",
                "tone": "amber",
            },
            {
                "idx": "P/05",
                "name": "Habit Tracker",
                "desc": {
                    "en": "Personal habits dashboard.",
                    "vi": "Dashboard theo dõi thói quen cá nhân.",
                },
                "url": "https://habits.kynguyen.cc",
                "url_label": "habits.kynguyen.cc",
                "stack": "next · sql",
                "tone": "violet",
            },
            {
                "idx": "P/06",
                "name": "Cà Phê Diary",
                "desc": {
                    "en": "Coffee log from cafés around Đà Nẵng.",
                    "vi": "Nhật ký cà phê từ các quán quanh Đà Nẵng.",
                },
                "url": "https://cafe.kynguyen.cc",
                "url_label": "cafe.kynguyen.cc",
                "stack": "next · md",
                "tone": "rose",
            },
        ],
    },
    # -----------------------------------------------------------------
    # elsewhere — 6 social/platform links
    # -----------------------------------------------------------------
    "elsewhere": {
        "section_num": {"en": "05 / elsewhere", "vi": "05 / liên hệ"},
        "title": {"en": "Find me online.", "vi": "Tìm tôi trên mạng."},
        "items": [
            {
                "icon": "github",
                "name": "GitHub",
                "handle": "@nguyenrot",
                "url": "https://github.com/nguyenrot",
            },
            {
                "icon": "linkedin",
                "name": "LinkedIn",
                "handle": "Nguyen Pham Ky",
                "url": "https://www.linkedin.com/in/nguyen-pham-ky",
            },
            {
                "icon": "facebook",
                "name": "Facebook",
                "handle": "@phkynguyen",
                "url": "https://www.facebook.com/phkynguyen",
            },
            {
                "icon": "instagram",
                "name": "Instagram",
                "handle": "@phkynguyen",
                "url": "https://www.instagram.com/phkynguyen",
            },
            {
                "icon": "threads",
                "name": "Threads",
                "handle": "@phkynguyen",
                "url": "https://www.threads.com/@phkynguyen",
            },
            {
                "icon": "tiktok",
                "name": "TikTok",
                "handle": "@phamkynguyen",
                "url": "https://www.tiktok.com/@phamkynguyen",
            },
        ],
    },
}


def seed_me_sections(apps, _schema_editor):
    SiteContent = apps.get_model("content", "SiteContent")
    for section, data in ME_SECTIONS.items():
        SiteContent.objects.update_or_create(
            app="me",
            section=section,
            defaults={"data": data},
        )


def unseed_me_sections(apps, _schema_editor):
    SiteContent = apps.get_model("content", "SiteContent")
    SiteContent.objects.filter(app="me", section__in=list(ME_SECTIONS.keys())).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_me_sections, unseed_me_sections),
    ]
