package com.armsx2.tirso

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.TypedValue
import android.view.Gravity
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.lifecycleScope
import com.armsx2.EmuState
import com.armsx2.runtime.MainActivityRuntime
import com.armsx2.ui.InGameOverlay
import com.armsx2.ui.WindowImpl
import com.swordfish.radialgamepad.library.RadialGamePad
import com.swordfish.radialgamepad.library.config.ButtonConfig
import com.swordfish.radialgamepad.library.config.CrossConfig
import com.swordfish.radialgamepad.library.config.PrimaryDialConfig
import com.swordfish.radialgamepad.library.config.RadialGamePadConfig
import com.swordfish.radialgamepad.library.config.SecondaryDialConfig
import com.swordfish.radialgamepad.library.event.Event
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.launch
import kr.co.iefriends.pcsx2.NativeApp
import org.json.JSONObject
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import kotlin.math.abs

/**
 * Tirso Compiler's player look for PS2 game apps.
 *
 * ARMSX2 still runs the game (engine, graphics, sound, memory cards, controllers), but its own
 * screens are replaced: Tirso's touch controls (the same ones as the N64/DS/PSP apps), Tirso's
 * pause menu, and a plain loading screen instead of ARMSX2's game library. Hooked in from
 * WindowImpl.Window (controls, menu) and MainActivityRuntime (loading screen, key/touch events).
 */
object TirsoPlayer {

    /** True in a Tirso game app (it carries assets/tirso/game.json). */
    @Volatile private var activeCache: Boolean? = null

    fun active(context: Context): Boolean = activeCache ?: runCatching {
        context.assets.open("tirso/game.json").close(); true
    }.getOrDefault(false).also { activeCache = it }

    val active: Boolean
        get() = activeCache ?: MainActivityRuntime.instance?.let { active(it) } ?: false

    /** The activity behind a Compose context (which may be wrapped for theming or scaling). */
    private fun activityOf(context: Context): ComponentActivity? {
        var c: Context? = context
        while (c is android.content.ContextWrapper) {
            if (c is ComponentActivity) return c
            c = c.baseContext
        }
        return MainActivityRuntime.instance
    }

    private fun config(context: Context): JSONObject = runCatching {
        JSONObject(context.assets.open("tirso/game.json").bufferedReader().use { it.readText() })
    }.getOrDefault(JSONObject())

    private fun title(context: Context) = config(context).optString("title", "").ifBlank {
        context.applicationInfo.loadLabel(context.packageManager).toString()
    }

    // ------------------------------------------------------------------ state
    private var overlay: FrameLayout? = null
    private var leftPad: RadialGamePad? = null
    private var rightPad: RadialGamePad? = null
    private var speedText: TextView? = null
    private var padsWanted = true
    private var showSpeed = false
    private var menuShowing = false
    private var selectDown = false
    private var startDown = false
    private val main = Handler(Looper.getMainLooper())

    private const val MENU_ID = -1000
    private const val LEFT_STICK = 0
    private const val RIGHT_STICK = 1
    private const val DPAD = 2
    private const val PORT = 0

    // ------------------------------------------------------------------ touch controls
    /** PlayStation 2 pad: D-pad, two analog sticks, ✕ ○ □ △, L1 L2 L3, R1 R2 R3, Select, Start. */
    private fun leftConfig() = RadialGamePadConfig(
        sockets = 12,
        primaryDial = PrimaryDialConfig.Cross(CrossConfig(DPAD)),
        secondaryDials = listOf(
            SecondaryDialConfig.SingleButton(1, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_THUMBL, label = "L3")),
            SecondaryDialConfig.SingleButton(2, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_L1, label = "L1")),
            SecondaryDialConfig.SingleButton(3, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_L2, label = "L2")),
            SecondaryDialConfig.SingleButton(4, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_SELECT, label = "SELECT")),
            SecondaryDialConfig.SingleButton(8, 1f, 0f, ButtonConfig(id = MENU_ID, label = "☰", contentDescription = "Menu")),
            SecondaryDialConfig.Stick(9, 2, 2.2f, 0.1f, LEFT_STICK, contentDescription = "Left stick",
                rotationProcessor = object : SecondaryDialConfig.RotationProcessor() {
                    override fun getRotation(rotation: Float): Float = rotation - 10f
                }),
        )
    )

    private fun rightConfig() = RadialGamePadConfig(
        sockets = 12,
        primaryDial = PrimaryDialConfig.PrimaryButtons(
            listOf(
                ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_B, label = "○"),   // right
                ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_Y, label = "△"),   // top
                ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_X, label = "□"),   // left
                ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_A, label = "✕"),   // bottom
            )
        ),
        secondaryDials = listOf(
            SecondaryDialConfig.SingleButton(2, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_START, label = "START")),
            SecondaryDialConfig.SingleButton(3, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_R2, label = "R2")),
            SecondaryDialConfig.SingleButton(4, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_R1, label = "R1")),
            SecondaryDialConfig.SingleButton(5, 1f, 0f, ButtonConfig(id = KeyEvent.KEYCODE_BUTTON_THUMBR, label = "R3")),
            SecondaryDialConfig.Stick(8, 2, 2.2f, 0.1f, RIGHT_STICK, contentDescription = "Right stick",
                rotationProcessor = object : SecondaryDialConfig.RotationProcessor() {
                    override fun getRotation(rotation: Float): Float = rotation + 10f
                }),
        )
    )

    private fun hasTouch(context: Context) =
        context.packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_TOUCHSCREEN)

    private fun hasGamepad(): Boolean = InputDevice.getDeviceIds().any { id ->
        val d = InputDevice.getDevice(id) ?: return@any false
        val s = d.sources
        !d.isVirtual && (s and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD ||
            s and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK)
    }

    /** The touch controls, drawn over the game in place of ARMSX2's. */
    @Composable
    fun Overlay() {
        val game = MainActivityRuntime.eState.value == EmuState.RUNNING || MainActivityRuntime.eState.value == EmuState.PAUSED
        AndroidView(factory = { ctx -> buildOverlay(activityOf(ctx) ?: ctx) }, modifier = Modifier.fillMaxSize(),
            update = { it.visibility = if (game) View.VISIBLE else View.GONE })
    }

    private fun buildOverlay(context: Context): FrameLayout {
        val root = FrameLayout(context)
        val left = RadialGamePad(leftConfig(), 8f, context).apply { gravityX = -1f; gravityY = 1f }
        val right = RadialGamePad(rightConfig(), 8f, context).apply { gravityX = 1f; gravityY = 1f }
        root.addView(left, FrameLayout.LayoutParams(0, 0, Gravity.BOTTOM or Gravity.START))
        root.addView(right, FrameLayout.LayoutParams(0, 0, Gravity.BOTTOM or Gravity.END))
        overlay = root; leftPad = left; rightPad = right
        padsWanted = hasTouch(context) && !hasGamepad()
        root.addOnLayoutChangeListener { _, l, t, r, b, ol, ot, or_, ob ->
            if (r - l != or_ - ol || b - t != ob - ot) arrange(r - l, b - t)
        }
        activityOf(context)?.lifecycleScope?.launch {
            merge(left.events(), right.events()).collect { onPadEvent(it) }
        }
        updatePads()
        if (!hasTouch(context) && !hasGamepad()) {
            Toast.makeText(context, "Connect a Bluetooth controller to play. Back opens the menu.", Toast.LENGTH_LONG).show()
        }
        return root
    }

    private fun arrange(w: Int, h: Int) {
        val portrait = h > w
        val size = if (portrait) (w * 0.5f).toInt() else minOf((h * 0.62f).toInt(), (w * 0.32f).toInt())
        for (pad in listOfNotNull(leftPad, rightPad)) {
            val lp = pad.layoutParams as FrameLayout.LayoutParams
            if (lp.width != size) { lp.width = size; lp.height = size; pad.layoutParams = lp }
        }
    }

    private fun updatePads() {
        val v = if (padsWanted) View.VISIBLE else View.GONE
        leftPad?.visibility = v
        rightPad?.visibility = v
        overlay?.let { if (it.width > 0) arrange(it.width, it.height) }
    }

    private val lastStick = Array(2) { IntArray(4) }   // x+, x-, y+, y- per stick
    private var lastDpad = Pair(0, 0)

    private fun onPadEvent(event: Event) {
        when (event) {
            is Event.Button -> {
                if (event.id == MENU_ID) {
                    if (event.action == KeyEvent.ACTION_DOWN) openMenu()
                } else {
                    val down = event.action == KeyEvent.ACTION_DOWN
                    if (down || event.action == KeyEvent.ACTION_UP)
                        NativeApp.setPadButtonForPort(PORT, event.id, 0, down)
                }
            }
            is Event.Direction -> when (event.id) {
                DPAD -> sendDpad(event.xAxis, event.yAxis)
                LEFT_STICK -> sendStick(0, intArrayOf(111, 113, 112, 110), event.xAxis, event.yAxis)
                RIGHT_STICK -> sendStick(1, intArrayOf(121, 123, 122, 120), event.xAxis, event.yAxis)
            }
            else -> {}
        }
    }

    private fun sendDpad(x: Float, y: Float) {
        val nx = if (x > 0.5f) 1 else if (x < -0.5f) -1 else 0
        val ny = if (y > 0.5f) 1 else if (y < -0.5f) -1 else 0
        val (ox, oy) = lastDpad
        fun set(key: Int, on: Boolean, was: Boolean) { if (on != was) NativeApp.setPadButtonForPort(PORT, key, 0, on) }
        set(KeyEvent.KEYCODE_DPAD_RIGHT, nx == 1, ox == 1)
        set(KeyEvent.KEYCODE_DPAD_LEFT, nx == -1, ox == -1)
        set(KeyEvent.KEYCODE_DPAD_DOWN, ny == 1, oy == 1)
        set(KeyEvent.KEYCODE_DPAD_UP, ny == -1, oy == -1)
        lastDpad = Pair(nx, ny)
    }

    /** Stick half-axes as ARMSX2's own touch sticks send them: one code per direction, 0..32767. */
    private fun sendStick(i: Int, codes: IntArray, x: Float, y: Float) {
        val next = intArrayOf(
            if (x > 0) (abs(x) * 32767).toInt() else 0, if (x < 0) (abs(x) * 32767).toInt() else 0,
            if (y > 0) (abs(y) * 32767).toInt() else 0, if (y < 0) (abs(y) * 32767).toInt() else 0,
        )
        for (k in 0 until 4) if (next[k] != lastStick[i][k]) {
            NativeApp.setPadButtonForPort(PORT, codes[k], next[k], next[k] > 0)
            lastStick[i][k] = next[k]
        }
    }

    // ------------------------------------------------------------------ input hooks (from MainActivityRuntime)
    /** Returns true when the key was used here (Select + Start opens the menu). */
    fun onKey(activity: Activity, event: KeyEvent): Boolean {
        if (!active(activity)) return false
        val fromPad = event.source and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD
        if (fromPad && padsWanted && !menuShowing) { padsWanted = false; updatePads() }
        if (event.keyCode == KeyEvent.KEYCODE_BUTTON_SELECT) selectDown = event.action == KeyEvent.ACTION_DOWN
        if (event.keyCode == KeyEvent.KEYCODE_BUTTON_START) startDown = event.action == KeyEvent.ACTION_DOWN
        if (selectDown && startDown && event.action == KeyEvent.ACTION_DOWN && !menuShowing && gameOn()) {
            selectDown = false; startDown = false
            NativeApp.setPadButtonForPort(PORT, KeyEvent.KEYCODE_BUTTON_SELECT, 0, false)
            NativeApp.setPadButtonForPort(PORT, KeyEvent.KEYCODE_BUTTON_START, 0, false)
            openMenu()
            return true
        }
        return false
    }

    fun onMotion(activity: Activity, ev: MotionEvent) {
        if (!active(activity)) return
        if (ev.source and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK && padsWanted && !menuShowing) {
            padsWanted = false; main.post { updatePads() }
        }
    }

    fun onTouch(activity: Activity, ev: MotionEvent) {
        if (!active(activity)) return
        if (ev.actionMasked == MotionEvent.ACTION_DOWN && !padsWanted && !menuShowing && hasTouch(activity)) {
            padsWanted = true; updatePads()
        }
    }

    private fun gameOn() = MainActivityRuntime.eState.value == EmuState.RUNNING || MainActivityRuntime.eState.value == EmuState.PAUSED

    // ------------------------------------------------------------------ loading screen
    /** Shown instead of ARMSX2's game library while the game starts (and if it can't). */
    @Composable
    fun StartingScreen() {
        val ctx = androidx.compose.ui.platform.LocalContext.current
        Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
            Text(title(ctx), color = Color(0xFF8C93A8), fontSize = 16.sp)
        }
    }

    // ------------------------------------------------------------------ pause menu
    private fun openMenu() {
        if (!gameOn()) return
        if (!WindowImpl.overlayVisible.value) InGameOverlay.open()   // pauses the game, then MenuHost shows the menu
    }

    /** Composed while ARMSX2's "menu open" state is on (Back button, menu hotkey, Select+Start, ☰). */
    @Composable
    fun MenuHost() {
        val ctx = androidx.compose.ui.platform.LocalContext.current
        LaunchedEffect(Unit) { activityOf(ctx)?.let { showMenu(it) } }
    }

    private fun closeMenu() {
        menuShowing = false
        if (WindowImpl.overlayVisible.value) InGameOverlay.toggle()   // closes and resumes
    }

    private fun dialog(activity: Activity) = AlertDialog.Builder(activity, android.R.style.Theme_DeviceDefault_Dialog_Alert)

    private fun showMenu(activity: Activity) {
        if (menuShowing) return
        menuShowing = true
        val touch = hasTouch(activity)
        val items = arrayOf(
            "Resume",
            "Save game state",
            "Load game state",
            if (!touch) "Controller help" else if (padsWanted) "Hide touch controls" else "Show touch controls",
            "Back up saves",
            "Restore saves",
            "Restart game",
            if (showSpeed) "Hide speed (fps)" else "Show speed (fps)",
            "Controller help",
            "Legal notices",
            "Exit game",
        )
        dialog(activity)
            .setTitle(title(activity))
            .setItems(items) { _, which ->
                when (which) {
                    0 -> closeMenu()
                    1 -> chooseSlot(activity, save = true)
                    2 -> chooseSlot(activity, save = false)
                    3 -> if (!touch) controllerHelp(activity) else { padsWanted = !padsWanted; updatePads(); closeMenu() }
                    4 -> backUpSaves(activity)
                    5 -> restoreSaves(activity)
                    6 -> confirm(activity, "Restart the game?", "Progress since your last in-game save will be lost.", "Restart") {
                        menuShowing = false
                        WindowImpl.overlayVisible.value = false
                        MainActivityRuntime.restart()
                    }
                    7 -> { showSpeed = !showSpeed; updateSpeed(activity); closeMenu() }
                    8 -> controllerHelp(activity)
                    9 -> legal(activity)
                    10 -> exitGame()
                }
            }
            .setOnCancelListener { closeMenu() }
            .show()
    }

    private fun stateFile(slot: Int): File? = runCatching { NativeApp.getGamePathSlot(slot) }.getOrNull()
        ?.takeIf { it.isNotBlank() }?.let { File(it) }?.takeIf { it.isFile }

    private fun chooseSlot(activity: Activity, save: Boolean) {
        val fmt = java.text.DateFormat.getDateTimeInstance(java.text.DateFormat.MEDIUM, java.text.DateFormat.SHORT)
        val labels = (1..3).map { slot ->
            val f = stateFile(slot)
            "Slot $slot  —  ${if (f != null) fmt.format(java.util.Date(f.lastModified())) else "empty"}"
        }.toTypedArray()
        dialog(activity)
            .setTitle(if (save) "Save to slot" else "Load from slot")
            .setItems(labels) { _, i ->
                val slot = i + 1
                if (!save && stateFile(slot) == null) { toast(activity, "Slot $slot is empty"); closeMenu(); return@setItems }
                Thread {
                    val ok = runCatching { if (save) NativeApp.saveStateToSlot(slot) else NativeApp.loadStateFromSlot(slot) }
                        .getOrDefault(false)
                    val busy = !ok && runCatching { NativeApp.isMemcardBusy() }.getOrDefault(false)
                    main.post {
                        toast(activity, when {
                            ok && save -> "Saved to slot $slot"
                            ok -> "Loaded slot $slot"
                            busy -> "The game is writing to its memory card. Try again in a few seconds."
                            save -> "Saving failed"
                            else -> "That save couldn't be loaded"
                        })
                        closeMenu()
                    }
                }.start()
            }
            .setOnCancelListener { closeMenu() }
            .show()
    }

    private fun confirm(activity: Activity, title: String, body: String, yes: String, onYes: () -> Unit) {
        dialog(activity).setTitle(title).setMessage(body)
            .setPositiveButton(yes) { _, _ -> onYes() }
            .setNegativeButton("Cancel") { _, _ -> closeMenu() }
            .setOnCancelListener { closeMenu() }
            .show()
    }

    private fun updateSpeed(activity: Activity) {
        val root = overlay ?: return
        val tv = speedText ?: TextView(activity).apply {
            setTextColor(0xFFEDE6D6.toInt()); textSize = 13f
            setBackgroundColor(0x99000000.toInt()); setPadding(16, 8, 16, 8)
            root.addView(this, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.START))
            speedText = this
        }
        tv.visibility = if (showSpeed) View.VISIBLE else View.GONE
        if (showSpeed) tick()
    }

    private fun tick() {
        val tv = speedText ?: return
        if (!showSpeed) return
        val fps = runCatching { NativeApp.getFPS() }.getOrDefault(0f)
        val speed = runCatching { NativeApp.getEmuSpeedPercent() }.getOrDefault(0f)
        tv.text = "%.0f fps · %.0f%% speed · ARMSX2".format(fps, speed)
        main.postDelayed({ tick() }, 1000)
    }

    private fun controllerHelp(activity: Activity) {
        dialog(activity).setTitle("Controller")
            .setMessage("Menu: Back, or Select + Start together.\nSticks, triggers and L3/R3 work as on a PS2 controller.\n" +
                "Touch the screen to bring the on-screen controls back.")
            .setPositiveButton("OK") { _, _ -> closeMenu() }
            .setOnCancelListener { closeMenu() }
            .show()
    }

    private fun legal(activity: Activity) {
        val text = "This game app runs ARMSX2 (a PlayStation 2 emulator based on PCSX2), free software under the " +
            "GNU General Public License v3. Source: https://github.com/ARMSX2/ARMSX2\n\n" +
            "Tirso Compiler's changes (this screen, the controls and the menu) are in the templates\\ps2\\source " +
            "folder of Tirso Compiler, under the same licence.\n\nOn-screen controls: RadialGamePad (Apache 2.0).\n\n" +
            "The game and the PS2 BIOS inside this app come from the owner's own discs and console."
        val pad = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 20f, activity.resources.displayMetrics).toInt()
        val tv = TextView(activity).apply { setText(text); textSize = 13f; setPadding(pad, pad / 2, pad, pad) }
        dialog(activity).setTitle("Legal notices")
            .setView(ScrollView(activity).apply { addView(tv) })
            .setPositiveButton("Close") { _, _ -> closeMenu() }
            .setOnCancelListener { closeMenu() }
            .show()
    }

    private fun exitGame() {
        menuShowing = false
        WindowImpl.overlayVisible.value = false
        MainActivityRuntime.closeGame()   // saves "continue" state, then closes the app
    }

    // ------------------------------------------------------------------ memory card backup
    private fun cardsDir(context: Context) = File(MainActivityRuntime.assetCopyRoot(context), "memcards")

    private const val REQ_EXPORT = 7301
    private const val REQ_IMPORT = 7302

    private fun safeName(context: Context) =
        title(context).replace(Regex("[^A-Za-z0-9]+"), "-").trim('-').ifBlank { "game" }

    private fun backUpSaves(activity: Activity) {
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/zip").putExtra(Intent.EXTRA_TITLE, "${safeName(activity)}-saves.zip")
        menuShowing = false
        runCatching { activity.startActivityForResult(intent, REQ_EXPORT) }.onFailure { closeMenu() }
    }

    private fun restoreSaves(activity: Activity) {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*").putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/zip", "application/octet-stream"))
        menuShowing = false
        runCatching { activity.startActivityForResult(intent, REQ_IMPORT) }.onFailure { closeMenu() }
    }

    /** From MainActivityRuntime.onActivityResult. Returns true when the result was ours. */
    fun onActivityResult(activity: Activity, requestCode: Int, resultCode: Int, data: Intent?): Boolean {
        if (requestCode != REQ_EXPORT && requestCode != REQ_IMPORT) return false
        val uri: Uri? = if (resultCode == Activity.RESULT_OK) data?.data else null
        if (uri == null) { closeMenu(); return true }
        if (requestCode == REQ_EXPORT) {
            val ok = runCatching {
                activity.contentResolver.openOutputStream(uri)?.use { out ->
                    ZipOutputStream(out).use { zip ->
                        zip.putNextEntry(ZipEntry("tirso-ps2-saves.txt"))
                        zip.write((activity.packageName + "\n").toByteArray()); zip.closeEntry()
                        cardsDir(activity).listFiles()?.filter { it.isFile }?.forEach { f ->
                            zip.putNextEntry(ZipEntry("memcards/" + f.name)); f.inputStream().use { it.copyTo(zip) }; zip.closeEntry()
                        }
                    }
                } ?: error("no stream")
            }.isSuccess
            toast(activity, if (ok) "Saves backed up" else "Backup failed")
            closeMenu()
        } else {
            val ok = runCatching {
                var marker = false
                val restored = mutableListOf<Pair<String, ByteArray>>()
                activity.contentResolver.openInputStream(uri)?.use { input ->
                    ZipInputStream(input).use { zip ->
                        while (true) {
                            val e = zip.nextEntry ?: break
                            if (e.name == "tirso-ps2-saves.txt") marker = true
                            else if (e.name.startsWith("memcards/") && !e.isDirectory && !e.name.contains(".."))
                                restored += e.name.removePrefix("memcards/") to zip.readBytes()
                        }
                    }
                }
                if (!marker || restored.isEmpty()) error("not a backup")
                val dir = cardsDir(activity).apply { mkdirs() }
                restored.forEach { (name, bytes) -> File(dir, File(name).name).writeBytes(bytes) }
            }.isSuccess
            if (ok) {
                Toast.makeText(activity, "Saves restored. Open the game again to use them.", Toast.LENGTH_LONG).show()
                menuShowing = false
                WindowImpl.overlayVisible.value = false
                // Next launch starts the game fresh (not from the "continue" state), so it reads the
                // restored memory card. TirsoBootActivity turns "continue" back on after that launch.
                activity.getSharedPreferences("tirso", Context.MODE_PRIVATE).edit().putBoolean("skip_autoload_once", true).commit()
                MainActivityRuntime.closeGame()
            } else {
                toast(activity, "That file isn't a save backup for this game")
                closeMenu()
            }
        }
        return true
    }

    @SuppressLint("ShowToast")
    private fun toast(context: Context, text: String) = Toast.makeText(context, text, Toast.LENGTH_SHORT).show()
}
