// MifBridge — logging category + DEBUG-gated log helper.
#pragma once

#include "CoreMinimal.h"
#include "HAL/IConsoleManager.h"

// All bridge output goes through this category. Default verbosity is Log; verbose
// request/response tracing is gated behind the `mif.BridgeDebug` console variable.
DECLARE_LOG_CATEGORY_EXTERN(LogMifBridge, Log, All);

// Toggled at runtime via `mif.BridgeDebug 1` (or `0`). Ships OFF.
extern TAutoConsoleVariable<bool> CVarMifBridgeDebug;

// DEBUG-gated verbose log. Emits at Log level only when the debug CVar is on, so
// runtime request/response state is never a guess when you flip the flag.
#define MIF_DBG(Fmt, ...) \
	do { \
		if (CVarMifBridgeDebug.GetValueOnAnyThread()) \
		{ \
			UE_LOG(LogMifBridge, Log, TEXT("[dbg] ") Fmt, ##__VA_ARGS__); \
		} \
	} while (0)
