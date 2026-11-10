/*
 * Created by Filippo Scognamiglio.
 * Copyright (c) 2022. This file is part of RadialGamePad.
 *
 * RadialGamePad is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * RadialGamePad is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with RadialGamePad.  If not, see <https://www.gnu.org/licenses/>.
 */

package com.swordfish.radialgamepad.library.haptics

import com.swordfish.radialgamepad.library.event.Event
import com.swordfish.radialgamepad.library.haptics.actuators.HapticActuator
import com.swordfish.radialgamepad.library.haptics.selectors.HapticSelector

class HapticEngine(
    private val selector: HapticSelector,
    private val actuator: HapticActuator
) {

    fun performHapticForEvents(events: List<Event>) {
        performHaptic(selector.getEffectConstant(events))
    }

    fun performHaptic(effect: Int) {
        actuator.performHaptic(effect)
    }

    companion object {
        const val EFFECT_NONE = 0
        const val EFFECT_RELEASE = 1
        const val EFFECT_PRESS = 2
    }
}
