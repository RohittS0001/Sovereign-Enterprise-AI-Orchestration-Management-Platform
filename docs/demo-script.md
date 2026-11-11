# Recording the README demo GIF (about 45 seconds)

A short silent GIF at the top of the README shows the product in seconds. Record it on the demo
data so no real company data appears.

## Setup (once)

1. Install **ScreenToGif** (free, Windows): <https://www.screentogif.com>.
2. Start the demo in two terminals:

   ```bash
   .venv/Scripts/python.exe -m scripts.dev demo
   ```

   ```bash
   npm --prefix frontend run dev
   ```

3. Open <http://localhost:5173>, sign in as `pm@example.com` (password printed by the demo command),
   switch to the **light** theme (moon/sun icon, top right), and set the browser window to about
   1280×800. Open **Knowledge** once and click **Index project data** so search covers Slack, Jira,
   e-mail and the rest.

## Recording (ScreenToGif → Recorder, 15 fps, frame over the browser content)

| Time | Do this | The viewer learns |
|---|---|---|
| 0–5 s | Start on **Home**. Pause a second on the KPIs and "Phoenix Payments Revamp · High risk". | One portfolio, one risky project |
| 5–10 s | Click **Phoenix Payments Revamp**, then the **Why is it late?** tab. | — |
| 10–18 s | Pause on the red verdict ("Projected … late. Main reason: waiting on an external party"). Move the mouse slowly over **Where the time went**. | The real reason, in person-days |
| 18–25 s | Scroll a little to **Reported vs actual**; rest on the Slack quote "keep reporting Phoenix as on track to leadership" marked **Contradicted by data**. | The data contradicts the status report |
| 25–33 s | In **Check what you were told** type `We're on track with no blockers` and click **Check against data**. Pause on the result. | Any claim can be checked |
| 33–42 s | Open **Knowledge**, choose **Everything**, search `are we telling leadership the real status`. Pause on the results with the Slack / Document / Meeting badges. | One search across every tool |
| 42–45 s | End on the results. | — |

## Export

- ScreenToGif editor: delete dead frames at the start and end, then **Save as → GIF**, quality
  "Medium", width 1100 px. Aim for under 8 MB (GitHub shows up to 10 MB inline).
- Save it as `docs/images/demo.gif` and add this line under the title in `README.md`:

  ```markdown
  ![45-second demo](docs/images/demo.gif)
  ```

The still screenshots in `docs/images/` were captured from the same demo with headless Edge.
