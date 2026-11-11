TIRSO COMPILER  1.1  -  N64, DS, GBA, PSP, PlayStation 1, PlayStation 2, GameCube, Wii
======================================================================================
Turns games you own into standalone Android apps: each app has its own name,
icon and saves, and opens straight into the game.

INSTALL IT AS A NORMAL WINDOWS PROGRAM (recommended)
  1. Double-click "Make Installer.bat" (needs the internet the first time;
     about 3-10 minutes). It checks the program, packages it and opens the
     "Installer" folder.
  2. Double-click "Tirso Compiler Setup 1.0.0.exe" there and follow the steps.
     No administrator rights are needed. You get a Start menu entry and,
     if you tick it, a desktop shortcut. Uninstall from Windows Settings > Apps.
  3. Your library, BIOS copies, GPU drivers, settings and signing key stay in
     %LOCALAPPDATA%\TirsoCompiler, so reinstalling or updating keeps them.
  The installer can be copied to another PC and installed there too.

HOW TO START WITHOUT INSTALLING
  Double-click "Start Tirso Compiler.bat".
  The first time, it installs what it needs (about 1-3 minutes, needs internet).
  After that it opens straight away.

WHAT WORKS NOW
  - ROM Library: add game files or whole folders (button or drag and drop).
    Games are identified from their contents: N64, DS, PSP, PS2, GameCube, Wii.
    Checksums (CRC32 + SHA-1), region, game code, duplicates, missing files.
    Your files are never moved, changed or deleted.
  - BIOS Manager: import PS2 (required) and DS (optional) BIOS files you dumped.
    Each one is verified and a private copy is kept on this PC.
  - Game Builder: console, game, compatibility report, app name, package ID,
    icon, orientation, BIOS and performance profile. Saved to My Games.
  - Engine Manager, Build Center, Settings.

NEW IN PHASE 2: REAL NINTENDO 64 APKs
  - My Games > Generate APK (or the last step of Game Builder).
  - Progress, log and result appear in Build Center. A build takes seconds.
  - The APK is saved in your output folder (Settings) as "<App name>.apk".
  - Every APK is signed with your own private key, created on the first build.
    Back it up from Engine Manager or Settings.

NEW IN PHASE 3: NINTENDO DS AND PSP
  - Nintendo DS (melonDS DS): choose a screen layout in Game Builder step 4:
      Turns with the phone (default: stacked upright, large top screen sideways,
      switching as you turn it), Stacked, Side by side, or Large top screen.
    Tap the bottom screen directly; the controls never cover it.
  - PSP (PPSSPP): ISO files are compressed to CSO automatically before
    packaging (about 30 seconds per GB, one time only). CSO files are used as-is.
  - Games must be under 1.5 GB after compression to fit in one APK.
  - Widescreen hack (on by default) and Resolution (Auto, 720p, 1080p, 1440p):
    Game Builder step 5. Resolution is for N64, PSP and PS2.

TV SUPPORT (N64, DS, PSP)
  - Game Builder step 5 > "TV support". The APK then also contains 32-bit
    engines (most TV devices need them), shows on the TV home screen with its
    own banner, and always opens sideways. It still works on phones.
  - DS on TV: large top screen left, touch screen right.
  - Install on TV with the free "Downloader" or "Send Files to TV" apps.
    Fire TV: only Fire OS models (e.g. Fire TV Stick 4K Plus); Vega OS models can't.
  - Play with a Bluetooth controller. Back on the remote, or Select + Start, opens the menu.
  - TV chips are much slower than phones. TV builds use lighter N64 graphics, and DS
    uses the DeSmuME engine on 32-bit TV devices. Pause menu > "Show speed (fps)"
    shows the real speed and which engine is running.

DS CONTROLS ON A CONTROLLER (phone or TV)
  - Left stick or D-pad = D-pad.
  - Right stick moves the touch-screen pencil; R2 touches.
  - R3 (press the right stick) swaps the two screens. On touch screens: the ⇄ button.

NEW IN PHASE 4: INSTALL FROM TIRSO (USB OR WI-FI)
  - Build Center > "Install on a phone or TV" (or "Install" next to any build).
  - The first time, Tirso downloads Google's ADB tool (8 MB, checksum-verified).
  - Phones: turn on USB debugging and plug in. TVs: connect over Wi-Fi
    (Google TV: Wireless debugging + pairing code; Fire TV: ADB debugging + IP).
    Step-by-step help is inside the install window.
  - Tirso checks the device first (32/64-bit, free space) and explains any
    install error in plain words. The game opens on the device when installed.

NEW IN PHASE 5: PLAYSTATION 2 (ARMSX2)
  - Import your own PS2 BIOS in BIOS Manager first. It goes inside each PS2 app,
    so the game starts with no setup on the phone.
  - PS2 games can be .iso, .cso, .chd or BIN/CUE (CD games). For BIN/CUE, add the
    .cue file (or the whole folder): its .bin files are found automatically and
    are not listed as separate games. Music tracks on the disc are left out.
  - PS2 games are compressed to CSO (one time, cached). Games up to 1.5 GB go
    inside the APK. Bigger games are saved as the APK plus a game data folder:
    "<Game> (game data)\<package>\game.cso" in your output folder.
    The Install button sends both. By hand: copy the <package> folder into
    Android/obb on the phone.
  - Widescreen uses ARMSX2's widescreen patches (only games that have one) + 16:9.
  - First launch of an app with the game inside takes a moment ("Preparing the
    game"): the game is unpacked once. The game app looks like the other Tirso
    games: same touch controls (D-pad, both sticks, L1-L3, R1-R2-R3) and the same
    pause menu (Back, the ☰ button, or Select + Start on a controller).
    Phones only (64-bit); best on Galaxy S24/S25.

NEW IN PHASE 6: GAMECUBE AND WII (DOLPHIN)
  - Same Tirso player as N64/DS/PSP: touch controls, pause menu, save states,
    save backups, Bluetooth controllers. Phones only (64-bit); best on S24/S25.
  - Game files: .iso / .gcm (compressed to GCZ automatically, one time only),
    .gcz, .rvz and .wbfs are used as they are. No BIOS needed.
  - Games over 1.5 GB (most Wii games) become the APK plus a game data folder,
    like big PS2 games. The Install button sends both.
  - Wii: choose the controller in Game Builder step 4: Wii Remote sideways,
    Wii Remote + Nunchuk (right stick = pointer), Classic Controller, or
    GameCube controller (only games that support it).
  - Widescreen (Wii 16:9 mode + Dolphin's widescreen hack) and Resolution
    (720p / 1080p / 1440p = Dolphin 2x / 3x / 4x) in step 5.

NEW: GAME BOY ADVANCE, BATCH BUILDS, FOLDERS PER CONSOLE
  - Game Boy Advance (mGBA): .gba files, no BIOS needed, phones and TV.
  - Build several games at once: in ROM Library tick the games (one console at a
    time), then "Build N games". Pick the settings once; each game becomes its own
    app. Builds run one after another; Build Center shows the queue ("Waiting")
    and can remove a game from it.
  - APKs are saved in one sub-folder per console inside the output folder,
    e.g. "Tirso Compiler APKs\Nintendo 64\Diddy Kong Racing.apk".
  - Resolution (720p / 1080p / 1440p) is offered with the High Quality profile.
  - In every game's pause menu: "Save problem report" saves a technical report
    to the phone's Downloads folder, to send to Claude when something goes wrong.

VERSION 1.1
  - Game information for single games too: Game Builder step 4 looks the game up and
    uses its real name and logo icon (you can still pick your own). "Wrong game?"
    lets you choose the right one.
  - Batch Build: "Settings for every game in this folder" (profile, resolution,
    widescreen, screen filter, GameCube/Wii graphics, DS screens, Wii controller).
  - TV builds are saved as "<Game> (For TV).apk" (the name on the TV stays the game's).
  - ROM Library: "Remove missing" removes games whose files are gone (their
    projects too, after asking). Adding a file again reads it again.
  - Light and dark look: Settings > Appearance (Light, Dark, or follow Windows).
  - Resolution can be chosen with every profile, not only High Quality.
  - Screen filter (GBA, DS, N64, PSP): Original, Sharp pixels, Smooth upscale, Handheld
    LCD, CRT TV. In the builder, and in every game's pause menu.
  - GameCube/Wii: choose Vulkan or OpenGL ES in the builder (still switchable in game).
  - In the games: two touch buttons at once now work on every phone (Galaxy S20 FE);
    "Hide touch controls" keeps them hidden until you press Back; TVs are asked for
    their game mode (less input delay), and N64 on TV no longer adds a frame of delay;
    DS games can use the phone's microphone (asked once; "Microphone" in the menu).
  - PS2 BIN/CUE: a .bin that was renamed without its .cue is found anyway, and
    BIN/CUE discs in a folder are added even when Tirso can't tell the console.
  - New console: PlayStation (PS1), played with SwanStation. Import your own PS1 BIOS
    first (BIOS Manager > PlayStation: scph5500/5501/5502 or any real PS1 BIOS dump).
    BIN/CUE discs with music tracks are joined into one file, music kept.
  - Cheats in every game's pause menu ("Cheats"): Tirso downloads the known cheats for
    each game while building (single games and Batch Build) and puts them inside the
    app. All start off; tick the ones you want and they stay on next time.
    N64, DS, GBA, PS1, PSP: RetroArch's cheat collection. PS2: two community .pnach
    collections, matched to your exact disc. GameCube/Wii: Dolphin's own cheat list.
    Cheats can crash or corrupt a game, so save first.

SAVE BACKUPS (FOR TIRSODRIVE)
  - Every game app copies its saves to Download/Tirso Saves on the phone whenever
    they change: "<Game> (<Console>) - saves.zip". In-game saves, memory cards and
    manual save states are included (not the automatic "where you left off" state).
  - The zip holds tirso-saves.json: {"format": 1, "package", "title", "console",
    "created"}, plus saves/ and states/ folders (PS2 games: memcards/ instead).
  - To restore, another app (TirsoDrive) opens the game with
    Intent("com.tirso.action.RESTORE_SAVES") and the zip as data (type
    application/zip, with read permission). The game asks before replacing saves.
  - "Back up saves" / "Restore saves" in the pause menu still work by hand.

BATCH BUILD TAB (A WHOLE FOLDER, WITH GAME INFORMATION AND LOGO ICONS)
  - Batch Build (sidebar): choose Phones or TV, then "Choose a folder". Sub-folders
    are included; files that aren't games are skipped.
  - Tirso identifies each game from its contents (fingerprint first, then the disc
    or cartridge code, then the file name, ignoring extras like "(Rev 1)" or
    "EnEsFr"), and looks up its details, description, box art, screenshots and logo.
  - App icon: the game's logo on a white background; box art when there's no logo
    (DS and PSP games have no logos in the picture library).
  - Check the list: "Wrong game?" picks the right one, "Rename" changes the app
    name, untick games you don't want, "Details" shows what was found. Then Build.
  - These games stay out of My Games and the ROM Library. Removing a folder only
    clears Tirso's list; your files and built apps stay.
  - Apps built this way have "Game information" in the pause menu (not in PS2 apps
    yet). Game lists and pictures download the first time (internet needed).
  - Sources, credited in each app: libretro-database and libretro thumbnails,
    OpenVGDB, Wikipedia.

CUSTOM GPU DRIVERS (PS2, GAMECUBE, WII)
  - BIOS Manager > "PS2 GPU driver" and "GameCube & Wii GPU driver": add an
    Adreno driver pack (.zip) you already have, for example Mesa Turnip by
    Mr Purple. Every app of those consoles built afterwards carries it and uses it
    with Vulkan on Snapdragon (Adreno) phones; other phones keep their own driver.
  - GameCube/Wii pause menu > Graphics: Vulkan (default, uses the driver),
    Vulkan with the phone's own driver, OpenGL ES, OpenGL ES exact textures,
    Check for errors. If a choice stops the game from starting, it goes back to
    OpenGL ES by itself.
  - Tirso never downloads drivers, games or BIOS files.

DOWNLOAD
  Latest release (always up to date):
  https://github.com/dioniciogarcia/tirsocompiler/releases/latest/download/TirsoCompiler.zip
  Unzip and double-click "Start Tirso Compiler.bat". The first run installs what
  it needs automatically. Your library, BIOS files and settings are kept separately
  and are never touched by an update.

UPDATING TIRSO COMPILER
  Open Settings in the program and click "Check for updates". If a new version
  is available, click "Update now". The program downloads and verifies it, then
  closes and reopens on its own. A blue dot on the Settings button means an update
  is waiting.

PUBLISHING A NEW VERSION (for the developer)
  1. Open app/core/updater.py and raise APP_VERSION (e.g. "1.1.0" → "1.2.0").
  2. Add a changelog section to this README (a line starting with "## 1.2.0").
  3. Push to GitHub. The build workflow starts automatically, compiles the zip,
     and publishes a GitHub Release tagged v1.2.0 with the changelog as notes.
  Example changelog section (the build reads the text between two ## headings):

## 1.1.0
- PlayStation 1 support (SwanStation).
- Cheats menu in every game.
- PS2 touch fix, hide controls, auto-backup saves.

LATER VERSIONS
  PlayStation 3 is planned for a later version.

INSTALLING ON A GALAXY PHONE BY HAND
  1. Copy the APK to the phone (USB cable, or Quick Share from Windows).
  2. On the phone, open My Files, find the APK and tap it.
  3. Allow My Files to install unknown apps if asked.
  4. If Play Protect warns about an unknown developer: More details > Install anyway.

IN EACH GAME APP
  - Opens straight into the game. Touch controls hide when a Bluetooth
    controller is used and come back when you touch the screen.
  - Back button (or the menu button on the touch pad) opens the pause menu:
    save/load states (3 slots), show/hide controls, back up/restore saves,
    restart, legal notices, exit.
  - The game continues where you left off when you reopen it.

FOLDERS
  Make Installer.bat   builds the Windows installer (see the top of this file)
  installer      installer script and program icon
  templates\n64, nds, gba, psp, ps2, dolphin   Android templates per console
  templates\source.zip   source code of the Tirso game player (GPL)
  templates\ps2\source   Tirso's changes to ARMSX2 and how to build it (GPL-3.0)
  tools          Java runtime, Apktool and APK signer used to package APKs

WHERE YOUR DATA IS
  %LOCALAPPDATA%\TirsoCompiler   (database, BIOS copies, icons, logs)
  Kept outside this folder so ROMs, BIOS files and keys never end up in shared code.

IF IT DOESN'T OPEN
  Look for %LOCALAPPDATA%\TirsoCompiler\logs\startup-error.log and send it to Claude.

SELF-TEST (optional)
  .venv\Scripts\python.exe -m tests.run_tests
