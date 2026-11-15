# Quickstart: Checking the Look Refresh

Items 1 to 6 need no lab. Items 7 and 8 need the lab or the maintainer.

## Without a lab

```bash
uv run invoke dashboard.build
uv run python dev/scripts/serve_dashboard_fixtures.py --port 18932 --sources up &
node packages/presentation/ui/e2e/look.mjs http://127.0.0.1:18932 /tmp/look
```

1. **First screen (SC-001).** Open `first-screen-light.png`. The number row and the whole fabric panel are visible.
2. **Length (SC-002, SC-003).** The script prints the page height and the offset of the first metrics chart. Compare with the baseline in tasks.md T003.
3. **Both themes.** Open the light and dark full-page pictures. Panels are lifted by shadow in light and by border in dark. No text is cut off.
4. **A device selected.** In `device-light.png` the drawer has taken the summary's place.
5. **Narrow.** Open the page at 360 px wide. The number row is one column and nothing scrolls sideways.
6. **Tests.** `uv run invoke dashboard.test` and `uv run invoke dashboard.e2e` pass.

## With the lab

7. **Live page.** With the demo up, open `snapl dashboard`. Induce a drift on one device and check the four cards, the ring and the ranked bars against `snapl status`. **Needs the lab.**
8. **Pictures.** Retake the README's, the dashboard guide's and spec 011's screenshots. **Needs the lab.**
