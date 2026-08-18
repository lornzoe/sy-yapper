# Twitch chat → Voicebox TTS → VB-Cable

Reads your Twitch chat and speaks each message out loud through
[Voicebox](https://github.com/jamiepine/voicebox), routed to a VB-Audio
Cable virtual device so OBS/Discord/etc. can pick it up like a mic input.

## Setting up from scratch

Everything below is a one-time setup. Budget half an hour, mostly downloads.

### 1. Python

Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/),
ticking **"Add python.exe to PATH"** in the installer.

### 2. VB-Audio Virtual Cable

Install [VB-Audio Virtual Cable](https://vb-audio.com/Cable/). This creates a
fake microphone: the bot plays speech into `CABLE Input`, and OBS listens on
`CABLE Output`. Reboot afterwards so Windows registers the device.

### 3. Voicebox

Install [Voicebox](https://github.com/jamiepine/voicebox) and launch it. It
serves a local API on `http://127.0.0.1:17493` that this bot talks to. Voicebox
must be **running** whenever the bot runs.

### 4. Download a TTS model

In Voicebox, open Settings -> Models and download **Chatterbox Turbo**
(~3.9 GB). It supports both preset and cloned voices, and it is what this
project is set up for out of the box.

Voicebox ships other engines too -- Chatterbox, TADA, Qwen, LuxTTS -- and you
can switch to any of them on the Voice tab once the model is downloaded. They
all work; Chatterbox Turbo is simply the default.

> **If the download fails at 0% with `[Errno 22] Invalid argument: ...config.json`**
> -- this is a known Voicebox bug on Windows, not something you did wrong. Its
> downloader cannot follow the symlinks HuggingFace puts in the model cache.
> Fix it with:
>
> ```powershell
> .venv\Scripts\python fix_hf_symlinks.py
> curl -X POST http://127.0.0.1:17493/tasks/clear
> ```
>
> Clearing the task state matters: a failed download leaves an error pinned in
> Voicebox that makes the model read as "not downloaded" forever. Do not press
> Download again, it just re-pins the error.

### 5. Create a voice profile

In Voicebox, make a voice:

- **Preset** (easiest): New Voice -> Preset -> pick an engine and a voice.
- **Cloned**: record or upload a sample of the voice you want.

Either works with Chatterbox Turbo. If you later switch to an engine that only
speaks preset voices, the app will stop you rather than let it fail at
synthesis time.

### 6. Install and run

Unzip this project somewhere, then in PowerShell from that folder:

```powershell
.\setup.ps1
```

That builds a `.venv` and installs dependencies. Then:

```powershell
.venv\Scripts\python main.py
```

The control panel opens. On the **Twitch** tab set your channel name, on the
**Voice** tab pick your profile (the engine auto-selects to match), then press
**Start**.

### 7. Hear it in OBS

Add an **Audio Input Capture** source in OBS and point it at **CABLE Output**.
Use the **Test tone** button on the Audio tab to confirm sound is arriving
before going live.

## Troubleshooting

**`[Errno 22] Invalid argument: ...\snapshots\<hash>\config.json`** — Voicebox's
bundled downloader/loader can't follow the symlinks that `huggingface_hub` puts
in `~/.cache/huggingface/hub`. The download fails at 0% and generation fails
with the same error, even though the files are on disk and readable.

Fix: replace those symlinks with hardlinks to the same blobs (no extra disk
use), then clear Voicebox's stuck task state:

```powershell
.venv\Scripts\python fix_hf_symlinks.py
curl -X POST http://127.0.0.1:17493/tasks/clear
```

A failed download leaves an `error` task pinned in `/tasks/active`, which makes
`/models/status` report `downloaded: false, downloading: true` forever. Clearing
tasks is what flips the model back to `downloaded: true` — do not press Download
again, it just re-pins the error.

## The control panel

Running `main.py` opens a window and starts the bot in it:

```powershell
.venv\Scripts\python main.py
```

- **Start / Stop** with a live status pill: `Warming up chatterbox_turbo - 12s`,
  `Listening to #yourchannel`, `Reconnecting...`, or a red `Error` banner
  carrying the actual reason.
- **Log tab** -- the same lines the CLI prints, with spoken messages in green
  and warnings in amber. Capped at 2000 lines so a long stream cannot bloat it.
  Each spoken line is logged as `[chatter said] their message`, so the log always
  names the speaker even when `SPEAK_USERNAME=false` keeps it out of the audio.
- **Closing the window stops the bot** and ends the process.
- **Twitch / Voice / Audio / Behavior tabs** -- every `.env` setting as a real
  control. Voice profiles and downloaded engines are fetched live from
  Voicebox; output devices come from the sound system. `Test connection` shows
  Voicebox's health, `Test tone` sends 400ms of sine to the selected device so
  you can confirm it reaches OBS.
- **Behavior tab preview** -- type a message and see exactly what would be
  spoken under the current settings.
- **Save to .env / Reload / Apply.** Saving preserves every comment and blank
  line, only rewriting the keys that actually changed, and takes a one-time
  `.env.bak` on the first save of a session.

`Apply` restarts the bot only when a setting needs it (channel, profile,
engine, device, queue size). The text-handling toggles -- username prefix,
emote and emoji stripping, max length, ignore list -- are read fresh on every
message and take effect without dropping the chat connection.

The profile and engine are checked against each other: pairing a cloned voice
with a preset-only engine is a guaranteed failure, so it blocks Start and says
why. Picking a profile auto-selects the engine it was built for.

### Running headless

The terminal-only path is unchanged:

```powershell
.venv\Scripts\python main.py --cli
```

`--no-autostart` opens the window without starting the bot.

## Emotes

`STRIP_EMOTES=true` (the default) removes Twitch's own emotes -- global, sub
and bits -- before anything is spoken. It does not guess from a word list:
Twitch tags every message with the exact character ranges its emotes occupy
(`emotes=25:0-4,6-10`), so removal is exact even for emotes whose names are
ordinary words. Ranges are indexed against the raw message, so stripping runs
before truncation and `/me` unwrapping.

`STRIP_EMOJI=true` removes unicode emoji as well.

A message left with nothing but emotes is skipped entirely rather than read as
a bare username:

```
Kappa that clutch was insane   ->  "someone says that clutch was insane"
lets goo Kappa Kappa Kappa     ->  "hype says lets goo"
Kappa Kappa                    ->  skipped
```

**Third-party emotes (BTTV / FFZ / 7TV) are not covered.** They never appear in
Twitch's tags -- to the IRC connection they are indistinguishable from ordinary
words, so filtering them needs a name list fetched from each service's API.
If a channel has no emote set registered with those services (their APIs
return 404 for it), only the services' *global* sets could ever show up.

## Warmup

The model is loaded lazily inside the first synthesis request, so without a
warmup the first chat message of the stream stalls behind it -- about 26s for
Chatterbox Turbo on CPU. `WARMUP=true` (the default) synthesizes a throwaway
phrase at startup and discards the audio, and also opens the output device
once. Measured here: first real message went from 26.4s to 1.1s.

Chat is still read and queued while the warmup runs, so nothing is missed --
messages just aren't spoken until the model is ready.

Set `WARMUP=false` to skip it, or `WARMUP_TEXT` to change the phrase.

## Notes

- Voicebox never rewrites the input text: the client omits `personality`, and
  the server defaults it to false. Turning it on makes an LLM answer the chat
  message in character rather than read it aloud, which is not what a chat
  reader wants.
- `VOICEBOX_ENGINE` picks which Voicebox TTS engine synthesizes the audio,
  defaulting to `chatterbox_turbo`. Voicebox's own API defaults to `qwen`,
  which is a much larger download and slow without a GPU, so the engine is
  sent explicitly on every request. Whatever you choose has to suit the
  profile named by `VOICEBOX_PROFILE`.

- Twitch chat is read anonymously (no OAuth token needed) by default. If
  that ever gets flaky, generate a token (`chat:read` scope) and set
  `TWITCH_NICK` + `TWITCH_OAUTH_TOKEN` in `.env`.
- `IGNORE_USERS` in `.env` filters out common chat bots by default
  (Nightbot, StreamElements, etc.) so they don't get read aloud.
- `MAX_MESSAGE_LENGTH` truncates very long messages; `MAX_QUEUE_SIZE` caps
  how many pending messages can back up before new ones are dropped.
