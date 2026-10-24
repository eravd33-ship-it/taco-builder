# Dimension widget (HyperFrames)

ווידג'ט "מימד 1 מתוך 3" כקומפוזיציית [HyperFrames](https://hyperframes.heygen.com): HTML שמתרנדר לווידאו.

```bash
npm run dev                                   # תצוגה מקדימה בדפדפן
npm run check                                 # lint + בדיקות layout/motion
npm run render -- -o renders/dimension-widget.mp4
npm run render -- --format webm --variables '{"showBackground":false}' -o renders/dimension-widget-overlay.webm
```

- `renders/dimension-widget.mp4` - עם רקע (1920×1080, 30fps, 6 שניות).
- `renders/dimension-widget-overlay.webm` - שקוף (alpha), להוספה כשכבה מעל משחק ב-OBS.
- טקסטים, תמונות הבלוקים והתקדמות הם משתנים ב-`compositions/widget.html` (`data-composition-variables`). כדי להחליף תמונה שימו קובץ ב-`assets/blocks/` ושנו את ערך המשתנה.
- תזמוני האנימציה בראש הסקריפט שם: `IN`, `STEP`, `HOLD_UNTIL`. ה-easing הוא `cubic-bezier(0.175, 0.885, 0.32, 1.275)`.
- GSAP נמצא מקומית ב-`assets/vendor/` (בלי תלות ב-CDN).
