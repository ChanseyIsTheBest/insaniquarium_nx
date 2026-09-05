/*
 * platform/switch/Input.cpp -- Switch input for the SexyAppFramework port,
 * driven by nx_pointer (stick cursor + touch + USB mouse + gyro).
 *
 * Replaces the original backend, which owned the pad and touchscreen itself.
 * nx_pointer now owns both, so this file is a translator: it turns NxpEvents
 * into WidgetManager mouse calls and handles the buttons nx_pointer does not
 * claim.
 *
 * Two things nx_pointer does not do, handled here:
 *
 *   1. HOVER. nx_pointer only emits events while a tap is held (see the
 *      `if (phase) push(...)` in nxp_update). A mouse-driven UI needs
 *      MouseMove without a button down or nothing ever highlights, so the
 *      cursor position is polled every frame and a MouseMove is synthesised
 *      when it changes.
 *
 *   2. RIGHT CLICK. Insaniquarium branches on `theClickCount < 0` in seven
 *      places (Board, Coin, Fish, FishTypePet, Breeder, Grubber, Penta), so
 *      right click is not optional. nx_pointer has one tap channel, so B is
 *      read from a second PadState here and fired at the cursor position.
 *      Two PadStates is legal in libnx: each keeps its own previous-state, so
 *      both see correct down/up edges.
 */
#include <switch.h>
#include <cstring>
#include <cstdio>

/* nx_pointer.h carries its own extern "C" guard, placed after its libc
 * includes. Do NOT wrap this include in another one: <stdio.h> and
 * <stdint.h> sit above that guard, and pulling them into an extern "C"
 * block is exactly the kind of thing that breaks on a toolchain bump. */
#include "nx_pointer.h"

#include "SexyAppBase.h"
#include "graphics/GLInterface.h"
#include "widget/WidgetManager.h"
#include "misc/KeyCodes.h"
#include "nx_datadir.h"

using namespace Sexy;

/* The EGL surface is created from nwindowGetDefault() with no explicit
 * dimensions, so it is 1280x720 in BOTH handheld and docked mode -- the system
 * scales to 1080p when docked. GLInterface::UpdateViewport() hardcodes the same
 * pair. So the cursor space never changes size and nx_pointer needs no
 * dock/undock resize handling. */
/* nx_pointer tags cursor-driven events with this id and touches with
 * 0..max_touch_slots-1. It is set in the config below and then compared
 * against in the event loop, so it is one constant rather than a literal
 * in two places that must agree. */
static const int kCursorId = 8;

static const int kScreenW = 1280;
static const int kScreenH = 720;

/* Buttons nx_pointer leaves free. nx_pointer claims: +, -, L, R, A, ZL, ZR,
 * D-pad up/down. */
static PadState sAuxPad;

static int sLastCursorX = -1;
static int sLastCursorY = -1;

static void NxpLog(const char *theMsg)
{
	/* nxlink picks this up with `nxlink -s`. */
	printf("%s", theMsg);
}

void SexyAppBase::InitInput()
{
	NxpConfig aConfig;
	memset(&aConfig, 0, sizeof aConfig);

	aConfig.screen_w        = kScreenW;
	aConfig.screen_h        = kScreenH;
	aConfig.panel_w         = 1280;   /* touch panel is 1280x720 */
	aConfig.panel_h         = 720;
	/* Wherever the game was actually installed -- resolved in main() from
	 * the .nro's own path. This is where an optional cursor.png is read
	 * from, so it lives with the rest of the game data. */
	aConfig.data_dir        = NxGetDataDir().c_str();
	aConfig.cursor_id       = kCursorId;
	aConfig.max_touch_slots = 8;
	aConfig.stick_speed     = 14.0f;
	aConfig.mouse_sens      = 1.0f;
	aConfig.log             = NxpLog;

	/* GLInterface::UpdateViewport() letterboxes the 4:3 game into the
	 * 1280x720 surface, giving a 960x720 rect at x=160. Without these the
	 * cursor can be walked into the black bars, where RemapMouse produces
	 * out-of-range coordinates and clicks silently do nothing. */
	const int aGameW = kScreenH * 4 / 3;
	aConfig.inset_left  = (kScreenW - aGameW) / 2;
	aConfig.inset_right = (kScreenW - aGameW) / 2;

	/* fopen_fn/fclose_fn are left NULL: this is a native build, not a
	 * so-loader, so there is no shared newlib handle table to serialise
	 * against. If the loading WorkerThread ever turns out to race the
	 * settings write, pass locked wrappers here. */

	nxp_init(&aConfig);   /* also does padConfigureInput + padInitializeDefault */

	padInitializeDefault(&sAuxPad);

	/* Belt and braces. The claim that this prevents a double cursor was wrong:
	 * EnforceCursor() only ever calls SDL_SetCursor, and SDL video is never
	 * initialised on Switch (Window.cpp builds the EGL surface directly), so
	 * the framework has no cursor here to collide with. Kept because it states
	 * the intent for any future platform where SDL video IS up, but do not
	 * expect it to be doing anything today. */
	EnableCustomCursors(false);

	if (!mMouseIn)
		mMouseIn = true;
}

bool SexyAppBase::StartTextInput(std::string& theInput)
{
	char aBuffer[512];

	SwkbdConfig aKbd;
	swkbdCreate(&aKbd, 0);
	swkbdConfigMakePresetDefault(&aKbd);
	swkbdConfigSetType(&aKbd, SwkbdType_Normal);

	swkbdConfigSetGuideText(&aKbd, "Enter text...");
	swkbdConfigSetOkButtonText(&aKbd, "OK");

	Result aResult = swkbdShow(&aKbd, aBuffer, sizeof(aBuffer));
	swkbdClose(&aKbd);

	if (R_SUCCEEDED(aResult))
	{
		theInput = aBuffer;
		return true;
	}

	return false;
}

void SexyAppBase::StopTextInput()
{
}

bool SexyAppBase::ProcessDeferredMessages(bool singleMessage)
{
	if (!appletMainLoop())
	{
		mShutdown = true;
		return false;
	}

	nxp_update();
	padUpdate(&sAuxPad);

	/* ---- hover -------------------------------------------------------
	 * Synthesised from the cursor position because nx_pointer stays silent
	 * unless a tap is held. Only sent when the position actually changed, so
	 * a still cursor costs nothing. */
	bool aCursorMoved = false;
	int aCursorX = 0, aCursorY = 0;

	if (nxp_cursor_visible())
	{
		float aFx = 0.0f, aFy = 0.0f;
		nxp_cursor_pos(&aFx, &aFy);
		aCursorX = (int)aFx;
		aCursorY = (int)aFy;

		if (aCursorX != sLastCursorX || aCursorY != sLastCursorY)
		{
			sLastCursorX = aCursorX;
			sLastCursorY = aCursorY;
			aCursorMoved = true;
		}
	}

	if (aCursorMoved)
	{
		int x = aCursorX, y = aCursorY;
		mWidgetManager->RemapMouse(x, y);
		mLastUserInputTick = mLastTimerTime;
		mWidgetManager->MouseMove(x, y);
	}

	/* ---- pointer events ----------------------------------------------
	 * Touch events (id < cursor_id) carry their own position, so they get a
	 * MouseMove of their own. Cursor events (id == cursor_id) only carry the
	 * button edge; their movement came from the hover block above. */
	NxpEvent anEvents[24];
	int aCount = nxp_poll(anEvents, 24);

	for (int i = 0; i < aCount; i++)
	{
		int x = (int)anEvents[i].x;
		int y = (int)anEvents[i].y;
		mWidgetManager->RemapMouse(x, y);

		mLastUserInputTick = mLastTimerTime;

		const bool isTouch = (anEvents[i].id != kCursorId);

		switch (anEvents[i].phase)
		{
			case NXP_DOWN:
				if (isTouch)
					mWidgetManager->MouseMove(x, y);
				mWidgetManager->MouseDown(x, y, 1);
				break;

			case NXP_MOVE:
				mWidgetManager->MouseMove(x, y);
				break;

			case NXP_UP:
				mWidgetManager->MouseUp(x, y, 1);
				break;
		}
	}

	/* ---- buttons nx_pointer does not claim ---------------------------- */
	const u64 aDown = padGetButtonsDown(&sAuxPad);
	const u64 aUp   = padGetButtonsUp(&sAuxPad);

	if (aDown || aUp)
		mLastUserInputTick = mLastTimerTime;

	/* B = right click, at wherever the cursor is. */
	if ((aDown | aUp) & HidNpadButton_B)
	{
		float aFx = 0.0f, aFy = 0.0f;
		nxp_cursor_pos(&aFx, &aFy);
		int x = (int)aFx, y = (int)aFy;
		mWidgetManager->RemapMouse(x, y);

		if (aDown & HidNpadButton_B)
		{
			mWidgetManager->MouseMove(x, y);
			mWidgetManager->MouseDown(x, y, -1);
		}
		if (aUp & HidNpadButton_B)
			mWidgetManager->MouseUp(x, y, -1);
	}

	/* X = Escape (back / menu). The original backend put this on '+', which
	 * nx_pointer now uses to toggle the cursor. */
	if (aDown & HidNpadButton_X) mWidgetManager->KeyDown(KEYCODE_ESCAPE);
	if (aUp   & HidNpadButton_X) mWidgetManager->KeyUp(KEYCODE_ESCAPE);

	/* Y = F1, which is what the port's Auto Collect feature listens for --
	 * the same key the PortMaster build binds Y to in insaniquarium.ini. */
	if (aDown & HidNpadButton_Y) mWidgetManager->KeyDown(KEYCODE_F1);
	if (aUp   & HidNpadButton_Y) mWidgetManager->KeyUp(KEYCODE_F1);

	/* StickL click = Enter, for dialogs that want a confirm key. */
	if (aDown & HidNpadButton_StickL) mWidgetManager->KeyDown(KEYCODE_RETURN);
	if (aUp   & HidNpadButton_StickL) mWidgetManager->KeyUp(KEYCODE_RETURN);

	/* No SDL event pump here on purpose. SexyAppBase in this branch has no
	 * HandleEvent(), and the framework's own Switch backend never polls either:
	 * SDL video is not initialised (Window.cpp builds the EGL surface directly)
	 * and SDL_mixer does not need the event queue. nx_pointer owns all input.
	 * An earlier version of this file drained SDL "to be safe" and would not
	 * compile. */

	return false;
}
