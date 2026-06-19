# Generated manually
from django.db import migrations

def add_tool_projects(apps, _schema_editor):
    SiteContent = apps.get_model("content", "SiteContent")
    
    # Update me projects
    me_projects = SiteContent.objects.filter(app="me", section="projects").first()
    if me_projects:
        data = me_projects.data
        items = data.get("items", [])
        if not any(item.get("idx") == "P/10" for item in items):
            data["aside_count"] = 10
            items.append({
                "idx": "P/10",
                "name": "Developer Tools",
                "desc": {
                    "en": "A collection of small tools, generators, and everyday utilities.",
                    "vi": "Tập hợp các công cụ nhỏ, generator và tiện ích hằng ngày.",
                },
                "url": "https://tool.kynguyen.cc",
                "url_label": "tool.kynguyen.cc",
                "stack": "nuxt · tools",
                "tone": "amber",
            })
            data["items"] = items
            me_projects.data = data
            me_projects.save()

    # Update vibe-hub landing-apps
    hub_apps = SiteContent.objects.filter(app="vibe-hub", section="landing-apps").first()
    if hub_apps:
        data = hub_apps.data
        if isinstance(data, list):
            if not any(item.get("id") == "tool" for item in data):
                data.append({
                    "id": "tool",
                    "number": "10",
                    "title": {"en": "Developer Tools", "vi": "Công cụ & Tiện ích"},
                    "altTitle": {"en": "Công cụ & Tiện ích", "vi": "Developer Tools"},
                    "blurb": {
                        "en": "A collection of small tools, generators, and everyday utilities.",
                        "vi": "Tập hợp các công cụ nhỏ, generator và tiện ích hằng ngày.",
                    },
                    "host": "tool.kynguyen.cc",
                    "link": "https://tool.kynguyen.cc",
                })
                hub_apps.data = data
                hub_apps.save()

class Migration(migrations.Migration):

    dependencies = [
        ("content", "0002_seed_me_sections"),
    ]

    operations = [
        migrations.RunPython(add_tool_projects, migrations.RunPython.noop),
    ]
