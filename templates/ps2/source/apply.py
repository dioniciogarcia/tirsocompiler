"""Applies the Tirso changes to the ARMSX2 Android app (run from platforms/android)."""
import re, shutil
from pathlib import Path

OV = Path(__file__).parent
APP = Path("app/src/main")
for f in (OV / "app/src/main").rglob("*"):
    if f.is_file():
        dest = APP / f.relative_to(OV / "app/src/main")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dest)

# icon background colour next to the game name string
t = APP / "res/values/tirso.xml"
s = t.read_text()
if "tirso_launcher_bg" not in s:
    s = s.replace("</resources>", '    <color name="tirso_launcher_bg">#FF0F121A</color>\n</resources>')
    t.write_text(s)

m = APP / "AndroidManifest.xml"
s = m.read_text()
if "TirsoBootActivity" not in s:
    # 1. ARMSX2's own splash is no longer a launcher entry (the game app has one icon: ours)
    s = s.replace('''            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.GAME" />
            </intent-filter>
''', "", 1)
    # 2. our boot screen is the launcher
    s = s.replace('''        <activity
            android:name=".BootSplashActivity"''', '''        <!-- Tirso Compiler: installs the embedded BIOS and game, then boots straight into it. -->
        <activity
            android:name="com.armsx2.tirso.TirsoBootActivity"
            android:configChanges="orientation|screenSize|screenLayout|smallestScreenSize|keyboard|keyboardHidden|navigation|uiMode"
            android:exported="true"
            android:noHistory="true"
            android:theme="@android:style/Theme.Black.NoTitleBar.Fullscreen">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.GAME" />
            </intent-filter>
        </activity>

        <activity
            android:name=".BootSplashActivity"''', 1)
    # 3. the game's own icon
    s = s.replace('android:icon="@mipmap/ic_launcher"', 'android:icon="@mipmap/tirso_launcher"', 1)
    s = s.replace('android:roundIcon="@mipmap/ic_launcher_round"', 'android:roundIcon="@mipmap/tirso_launcher"', 1)
    assert s.count("TirsoBootActivity") == 1 and "@mipmap/tirso_launcher" in s
    assert s.count("android.intent.category.LAUNCHER") == 1, "expected exactly one launcher entry"
    m.write_text(s)
print("Tirso overlay applied")

# ---- Tirso player look: Tirso's touch controls and pause menu instead of ARMSX2's screens ----
def patch(path, pairs):
    f = APP / path
    t = f.read_text()
    for old, new, marker in pairs:
        if marker in t:
            continue
        assert t.count(old) == 1, "%s: expected one '%s'" % (path, old[:60])
        t = t.replace(old, new)
    f.write_text(t)

patch("java/com/armsx2/ui/WindowImpl.kt", [
    ("                    com.armsx2.ui.touch.TouchControlsOverlay()\n",
     "                    if (com.armsx2.tirso.TirsoPlayer.active) com.armsx2.tirso.TirsoPlayer.Overlay()\n"
     "                    else com.armsx2.ui.touch.TouchControlsOverlay()\n", "TirsoPlayer.Overlay()"),
    ("                com.armsx2.ui.emulation.EmulationMenuScreen()\n",
     "                if (com.armsx2.tirso.TirsoPlayer.active) com.armsx2.tirso.TirsoPlayer.MenuHost()\n"
     "                else com.armsx2.ui.emulation.EmulationMenuScreen()\n", "TirsoPlayer.MenuHost()"),
])
patch("java/com/armsx2/runtime/MainActivityRuntime.kt", [
    ("                                com.armsx2.navigation.AppNavigation()\n",
     "                                if (com.armsx2.tirso.TirsoPlayer.active) com.armsx2.tirso.TirsoPlayer.StartingScreen()\n"
     "                                else com.armsx2.navigation.AppNavigation()\n", "TirsoPlayer.StartingScreen()"),
    ("    override fun dispatchKeyEvent(event: KeyEvent): Boolean {\n",
     "    override fun dispatchKeyEvent(event: KeyEvent): Boolean {\n"
     "        if (com.armsx2.tirso.TirsoPlayer.onKey(this, event)) return true\n", "TirsoPlayer.onKey"),
    ("    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {\n",
     "    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {\n"
     "        com.armsx2.tirso.TirsoPlayer.onTouch(this, ev)\n", "TirsoPlayer.onTouch"),
    ("    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {\n",
     "    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {\n"
     "        com.armsx2.tirso.TirsoPlayer.onMotion(this, ev)\n", "TirsoPlayer.onMotion"),
    ("    override fun onResume() {\n        super.onResume()\n",
     "    @Suppress(\"DEPRECATION\")\n"
     "    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {\n"
     "        super.onActivityResult(requestCode, resultCode, data)\n"
     "        com.armsx2.tirso.TirsoPlayer.onActivityResult(this, requestCode, resultCode, data)\n"
     "    }\n\n"
     "    override fun onResume() {\n        super.onResume()\n", "TirsoPlayer.onActivityResult"),
])
patch("java/com/armsx2/Pasx2Application.kt", [
    ("\t\tsuper.onCreate()\n\t\tcom.armsx2.bugreport.BugReport.init(this)\n",
     "\t\tsuper.onCreate()\n\t\tcom.armsx2.tirso.TirsoPlayer.active(this)  // Tirso game app? (decided before any screen)\n"
     "\t\tcom.armsx2.bugreport.BugReport.init(this)\n", "TirsoPlayer.active(this)"),
])
# RadialGamePad (the touch controls the other Tirso players use), from JitPack
root = APP.parent.parent.parent
g = root / "app/build.gradle.kts"
t = g.read_text()
if "radialgamepad" not in t:
    t = t.replace("dependencies {\n", "dependencies {\n    implementation(\"com.github.swordfish90:radialgamepad:08d1dd95\")  // Tirso touch controls\n", 1)
    g.write_text(t)
st = root / "settings.gradle.kts"
t = st.read_text()
if "jitpack" not in t:
    t = t.replace("        mavenCentral()\n        // Discord", "        mavenCentral()\n        maven { url = uri(\"https://jitpack.io\") }  // Tirso: RadialGamePad\n        // Discord", 1)
    assert "jitpack" in t
    st.write_text(t)
print("Tirso player look applied")
