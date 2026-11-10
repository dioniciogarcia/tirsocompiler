package com.armsx2.tirso

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import org.json.JSONObject
import java.io.File

/**
 * Tirso Compiler front door for a PS2 game app.
 *
 * 1. Puts the embedded BIOS where ARMSX2 boots from (externalFilesDir/bios).
 * 2. Unpacks the embedded game once, or uses the separate game data file that Tirso
 *    installs into Android/obb/<package>/ for games too big for one APK.
 * 3. Sets ARMSX2's options for a single-game app (no intro video, save on exit,
 *    resume on start, closing the game closes the app).
 * 4. Starts ARMSX2 with the game path. ARMSX2 sees a BIOS and a game already in
 *    place, skips its setup wizard, and boots straight into the game.
 */
class TirsoBootActivity : Activity() {

    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER }
        box.addView(ProgressBar(this))
        status = TextView(this).apply {
            setTextColor(0xFF8C93A8.toInt()); textSize = 14f; gravity = Gravity.CENTER; setPadding(40, 30, 40, 0)
        }
        box.addView(status)
        root.addView(box, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        setContentView(root)

        val config = try {
            JSONObject(assets.open("tirso/game.json").bufferedReader().use { it.readText() })
        } catch (e: Exception) {
            return fail("This app's game settings are missing. Build it again in Tirso Compiler.")
        }
        status.text = config.optString("title", "")
        requestedOrientation = when (config.optString("orientation", "landscape")) {
            "portrait" -> android.content.pm.ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
            "auto" -> android.content.pm.ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR
            else -> android.content.pm.ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        }

        Thread {
            // One preparation at a time: if Android restarts this screen (for example when it turns
            // to landscape) while files are still being copied, the second run waits for the first.
            synchronized(PREPARE_LOCK) { prepare(config) }
        }.start()
    }

    private fun prepare(config: JSONObject) {
        run {
            try {
                installBios(config)
                val game = findGame(config) ?: return@run
                applyDefaults()
                applyWidescreen(config.optBoolean("widescreen", false))
                applyResolution(config.optInt("upscale", 1))
                applyOrientation(config.optString("orientation", "landscape"))
                applyGpuDriver(config)
                runOnUiThread { launch(game) }
            } catch (e: Exception) {
                runOnUiThread { fail("The game couldn't be prepared. Check that the device has free storage.\n\n${e.message}") }
            }
        }
    }

    private fun filesRoot(): File = getExternalFilesDir(null) ?: dataDir

    private fun installBios(config: JSONObject) {
        val name = config.optString("bios", "").ifBlank { return }
        val dir = File(filesRoot(), "bios").apply { mkdirs() }
        val target = File(dir, "tirso-" + name)
        val size = config.optLong("bios_size", -1)
        if (target.exists() && (size < 0 || target.length() == size)) return
        dir.listFiles()?.filter { it.name.startsWith("tirso-") }?.forEach { it.delete() }
        copyAsset("tirso/$name", target, size)
    }

    /** The embedded game (unpacked once), or the data file Tirso put in Android/obb. */
    private fun findGame(config: JSONObject): File? {
        val rom = config.optString("rom", "")
        val size = config.optLong("rom_size", -1)
        val marker = config.optString("rom_sha1", "") + ":" + size
        val prefs = getSharedPreferences("tirso", Context.MODE_PRIVATE)
        if (rom.isNotBlank()) {
            val dir = File(filesRoot(), "tirso-game").apply { mkdirs() }
            val target = File(dir, rom)
            if (target.exists() && target.length() == size && prefs.getString("rom_marker", null) == marker) return target
            dir.listFiles()?.forEach { it.delete() }
            runOnUiThread { status.text = config.optString("title") + "\n\nPreparing the game (first launch only)…" }
            copyAsset("tirso/$rom", target, size)
            prefs.edit().putString("rom_marker", marker).apply()
            return target
        }
        val dataName = config.optString("data_file", "")
        val candidates = listOfNotNull(obbDir, getExternalFilesDir(null)).map { File(it, dataName) }
        val found = candidates.firstOrNull { it.isFile && (size < 0 || it.length() == size) }
        if (found != null) return found
        val wrongSize = candidates.firstOrNull { it.isFile }
        runOnUiThread {
            fail(if (wrongSize != null)
                "The game data file on this device is incomplete or from a different build. Install the game again from Tirso Compiler."
            else
                "This game's data file isn't on this device yet.\n\nIn Tirso Compiler, use \"Install on a phone or TV\" (it sends the data file too), " +
                    "or copy $dataName with a USB cable into:\nAndroid/obb/$packageName/")
        }
        return null
    }

    private fun copyAsset(asset: String, target: File, size: Long) {
        val tmp = File(target.parentFile, target.name + ".part")
        assets.open(asset).use { input -> tmp.outputStream().use { input.copyTo(it, 1 shl 20) } }
        if (size >= 0 && tmp.length() != size) {
            val got = tmp.length(); tmp.delete()
            error("size mismatch for $asset (copied $got bytes, expected $size)")
        }
        target.delete()
        if (!tmp.renameTo(target)) error("could not move $asset into place")
    }

    /** One-time ARMSX2 settings for a single-game app; the user can still change them in its menu. */
    private fun applyDefaults() {
        val tirso = getSharedPreferences("tirso", Context.MODE_PRIVATE)
        // Every launch: ARMSX2's own menu sounds, pause/library music and pop-up notes are off,
        // because Tirso's screens replace its menus (see TirsoPlayer).
        val armsx2 = getSharedPreferences("ARMSX2", Context.MODE_PRIVATE).edit()
            .putBoolean("pauseMusic.enabled", false)
            .putBoolean("ui.libraryMusic", false)
            .putBoolean("ui.menuSfx", false)
            .putBoolean("ui.hotkeyToasts", false)
        // After "Restore saves": one fresh start so the game reads the restored memory card.
        if (tirso.getBoolean("skip_autoload_once", false)) {
            armsx2.putBoolean("autoLoadOnBoot", false)
            tirso.edit().remove("skip_autoload_once").putBoolean("autoload_back_on", true).apply()
        } else if (tirso.getBoolean("autoload_back_on", false)) {
            armsx2.putBoolean("autoLoadOnBoot", true)
            tirso.edit().remove("autoload_back_on").apply()
        }
        armsx2.commit()
        if (tirso.getBoolean("defaults_applied", false)) return
        getSharedPreferences("ARMSX2", Context.MODE_PRIVATE).edit()
            .putBoolean("ui.bootLogo", false)
            .putBoolean("ui.exitToLauncherExternal", true)
            .putBoolean("autoSaveOnExit", true)
            .putBoolean("autoLoadOnBoot", true)
            .commit()
        tirso.edit().putBoolean("defaults_applied", true).apply()
    }

    /**
     * Widescreen chosen in Tirso: ARMSX2's widescreen patches (from its bundled patches.zip)
     * plus a 16:9 picture. Written into ARMSX2's global settings whenever the choice in a new
     * build differs from what was applied before; the in-game menu can still change it.
     */
    private fun applyWidescreen(wide: Boolean) {
        val tirso = getSharedPreferences("tirso", Context.MODE_PRIVATE)
        val wanted = if (wide) "on" else "off"
        if (tirso.getString("widescreen_applied", null) == wanted) return
        if (!wide && tirso.getString("widescreen_applied", null) == null) {
            tirso.edit().putString("widescreen_applied", wanted).apply(); return  // ARMSX2's own defaults
        }
        val prefs = getSharedPreferences("ARMSX2", Context.MODE_PRIVATE)
        val global = try { JSONObject(prefs.getString("config.global", null) ?: "{}") } catch (_: Exception) { JSONObject() }
        global.put("enablePatches", true)
        global.put("enableWideScreenPatches", wide)
        global.put("aspectRatio", if (wide) 3 else 1)   // 3 = 16:9, 1 = Auto 4:3/3:2
        prefs.edit().putString("config.global", global.toString()).commit()
        tirso.edit().putString("widescreen_applied", wanted).apply()
    }

    /**
     * Resolution chosen in Tirso: ARMSX2's resolution multiplier (1 = original PS2 resolution,
     * 2 ~ 720p, 3 ~ 1080p, 4 ~ 1440p). Applied when a new build asks for a different one.
     */
    /**
     * A GPU driver pack (Mesa Turnip etc.) the user put in this game with Tirso Compiler.
     * Installed through ARMSX2's own driver manager and switched on together with Vulkan,
     * only on Adreno GPUs (the phones these driver packs are made for).
     */
    private fun applyGpuDriver(config: JSONObject) {
        val asset = config.optString("gpu_driver", "")
        val tirso = getSharedPreferences("tirso", Context.MODE_PRIVATE)
        val prefs = getSharedPreferences("ARMSX2", Context.MODE_PRIVATE)
        val applied = tirso.getString("gpu_driver_applied", null)
        fun saveGlobal(change: (JSONObject) -> Unit) {
            val global = try { JSONObject(prefs.getString("config.global", null) ?: "{}") } catch (_: Exception) { JSONObject() }
            change(global)
            prefs.edit().putString("config.global", global.toString()).commit()
        }
        if (asset.isEmpty()) {
            if (applied != null) {  // an update of this game no longer carries a driver
                saveGlobal { it.put("customDriverId", "") }
                tirso.edit().remove("gpu_driver_applied").apply()
            }
            return
        }
        val stamp = config.optString("gpu_driver_sha1", asset)
        val installed = runCatching { com.armsx2.CustomDriver.listInstalled(this) }.getOrDefault(emptyList())
        val existing = installed.firstOrNull { it.id == tirso.getString("gpu_driver_id", "") }
        if (applied == stamp && existing != null) return
        if (applied == "skip:$stamp") return
        val gpu = runCatching { com.armsx2.GpuInfo.rendererName() }.getOrNull().orEmpty()
        if (!gpu.contains("Adreno", ignoreCase = true)) {
            android.util.Log.i("TirsoBoot", "GPU driver not used: this GPU is '$gpu'")
            tirso.edit().putString("gpu_driver_applied", "skip:$stamp").apply()
            return
        }
        val zip = File(cacheDir, "tirso_gpu_driver.zip")
        try {
            copyAsset("tirso/$asset", zip, -1)
            val driver = com.armsx2.CustomDriver.installFromUri(this, android.net.Uri.fromFile(zip))
            if (driver == null) {
                android.util.Log.w("TirsoBoot", "GPU driver couldn't be installed; using the phone's driver")
                return
            }
            saveGlobal {
                it.put("customDriverId", driver.id)
                it.put("renderer", "vulkan")
            }
            // ARMSX2 also seeds these two from older pref keys on its first start: keep them in step.
            prefs.edit().putString("renderer", "vulkan").putString("customDriverId", driver.id).commit()
            tirso.edit().putString("gpu_driver_applied", stamp).putString("gpu_driver_id", driver.id).apply()
            android.util.Log.i("TirsoBoot", "GPU driver on: ${driver.name} ${driver.version}")
        } finally {
            zip.delete()
        }
    }

    private fun applyResolution(upscale: Int) {
        val tirso = getSharedPreferences("tirso", Context.MODE_PRIVATE)
        val wanted = upscale.coerceIn(1, 8)
        if (tirso.getInt("upscale_applied", 1) == wanted) return
        val prefs = getSharedPreferences("ARMSX2", Context.MODE_PRIVATE)
        val global = try { JSONObject(prefs.getString("config.global", null) ?: "{}") } catch (_: Exception) { JSONObject() }
        global.put("upscaleFloat", wanted.toDouble())
        prefs.edit().putString("config.global", global.toString()).commit()
        tirso.edit().putInt("upscale_applied", wanted).apply()
    }

    /**
     * Screen orientation chosen in Tirso (Landscape / Portrait / Auto-rotate), every launch:
     * ARMSX2's game rotation (global settings) and its rotation for the loading screen.
     */
    private fun applyOrientation(choice: String) {
        val value = when (choice) { "portrait" -> 2; "auto" -> 3; else -> 1 }   // ARMSX2: 1 landscape, 2 portrait, 3 sensor
        val prefs = getSharedPreferences("ARMSX2", Context.MODE_PRIVATE)
        val global = try { JSONObject(prefs.getString("config.global", null) ?: "{}") } catch (_: Exception) { JSONObject() }
        val edit = prefs.edit()
        if (global.optInt("orientation", -1) != value) {
            global.put("orientation", value)
            edit.putString("config.global", global.toString())
        }
        edit.putInt("ui.launcherOrientation", value).putInt("ui.orientation", value).commit()
    }

    private fun launch(game: File) {
        startActivity(Intent(this, com.armsx2.Main::class.java)
            .putExtra("path", game.absolutePath)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
        overridePendingTransition(0, 0)
        finish()
    }

    private fun fail(message: String) {
        if (isFinishing) return
        AlertDialog.Builder(this)
            .setTitle(getString(applicationInfo.labelRes))
            .setMessage(message)
            .setCancelable(false)
            .setPositiveButton("Close") { _, _ -> finishAndRemoveTask() }
            .show()
    }

    private companion object {
        val PREPARE_LOCK = Any()
    }
}
