# 🎬 Creator Desk

אפליקציה למחשב (ובוט טלגרם) לניהול ערוץ יוטיוב: תזכורות לצילום, לעריכה ולהעלאה, מוטיבציה ויעדים, רעיונות, עוזר בינה מלאכותית, וחיבור ליוטיוב ולגוגל דרייב.

**האתר:** https://eravd33-ship-it.github.io/taco-builder (GitHub Pages, מהתיקייה הראשית של הענף `main`)

הקוד של האפליקציה נמצא ב-[`creator-bot/`](creator-bot/README.md).

## מבנה האתר

האתר הוא קבצי HTML/CSS/JS סטטיים, בלי build ובלי תלויות.

| קובץ | תוכן |
| --- | --- |
| `index.html` | דף הבית: מה האפליקציה עושה, צילומי מסך, פרטיות, שאלות נפוצות |
| `download.html` | הורדה והוראות התקנה. כפתורי ההורדה מופיעים לבד כשיש [Release](https://github.com/eravd33-ship-it/taco-builder/releases) ב-GitHub, ובלי Release מוצגות הוראות להורדה מ-Actions |
| `guide.html` | מדריך שימוש: מסכים, תזכורות, חיבור לגוגל, העוזר, נתונים ופרטיות, פתרון בעיות |
| `taco.html` | 🌮 בנה טאקו, האתר המקורי |
| `live/` | 🎙️ **כתוביות חיות**: אפליקציית ווב לטלפון שמתרגמת דיבור לכתוביות בעברית, בזמן אמת. [תיעוד](live/README.md) |
| `404.html`, `robots.txt`, `sitemap.xml` | עמוד שגיאה וקבצים למנועי חיפוש |
| `assets/site.css`, `assets/site.js` | העיצוב (בהיר וכהה אוטומטית) והסקריפט של דף ההורדה |
| `assets/img/` | הסמל, צילומי המסך ותמונת השיתוף |

## צילומי המסך

צילומי המסך באתר צולמו מהאפליקציה עצמה, עם נתוני דוגמה בלבד. כדי לצלם מחדש אחרי שינוי בעיצוב האפליקציה:

```bash
pip install playwright pillow && python -m playwright install chromium
cd creator-bot && python packaging/make_site_screenshots.py
```

## הרצה מקומית

```bash
python3 -m http.server 8000
```

ואז לפתוח את http://localhost:8000.
