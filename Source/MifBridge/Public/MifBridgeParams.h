// MifBridge — the parameter-handling contract, shared with provider plugins.
//
// WHY THIS HEADER EXISTS. MifBridgeEndpointRegistry.h lets a provider plugin register endpoints onto
// this bridge's HTTP surface. It did not let the provider handle PARAMETERS the way the bridge does:
// RejectUnknownParams, the type-strict JSON accessors, Fail, and MifDeferToNextTick were all declared
// in Private/MifBridgeHandlers.h, unreachable from another module. So a provider had exactly two
// options - reimplement them, or do without.
//
// MifKismetReconstructor took the first, deliberately and in writing, and both costs then landed:
//
//   * ITS BOOL READER DRIFTED. `KrJBool` used TryGetBoolField, which succeeds ONLY for a JSON
//     boolean, where JBool below also accepts 0/1 and the true/yes/on/1 spellings and REPORTS
//     anything else through RecordParamTypeViolation. So {"cookedOnly":"false"} silently kept its
//     `true` default and answered ok:true - across 13 bool parameters. The same client, on the same
//     port, got lenient parsing from mif_* and strict-silent parsing from kr_*.
//   * IT HAD NO UNATTENDED BACKSTOP. GIsRunningUnattendedScript appeared ZERO times in that plugin,
//     and five SetTimerForNextTick lambdas would not have been covered even if it had - a TGuardValue
//     restores on scope exit, so deferred work runs after the guard is gone. MifDeferToNextTick
//     re-arms it INSIDE the lambda and was equally unreachable.
//
// Neither was a careless mistake; both were the predictable result of a private helper and a public
// registrar. MifBridgeEndpointRegistry.h's own header says it best, about stale line citations: "a
// wrong citation is the MECHANISM of the duplicate-helper bug class - the next reader jumps to the
// cited line, finds nothing, and writes a local copy." A private declaration is that same mechanism
// with the jump made impossible.
//
// THE RULE THIS ENCODES. Extracting a shared helper only helps once the copies are DELETED. Shipping
// this header while leaving the Kr* mirrors in place would have been worse than either alone - two
// implementations plus the appearance of one. The mirrors went in the same change.
//
// WHAT IS DELIBERATELY NOT HERE. Handler registration (MifBridgeEndpointRegistry.h), the write-mode
// safety gate, the server, and anything that reads module-startup state. Providers load EARLIER than
// MifBridge - see that header's HARD RULE - so nothing reachable from here may touch the server, the
// routes, the menus or the token. Every function below is a pure function of its arguments, which is
// what makes it safe to call from a provider's module-startup path.

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

#include <initializer_list>

namespace MifBridge
{
	// --- Result helpers ------------------------------------------------------
	/** Set ok:false and error on the response. The bridge's ONE failure shape. */
	MIFBRIDGE_API void Fail(const TSharedRef<FJsonObject>& Out, const FString& Message);
	/** Reads the ok field, defaulting to true when absent - failure is the PRESENCE of an error. */
	MIFBRIDGE_API bool IsOk(const TSharedRef<FJsonObject>& Out);

	// --- JSON field accessors (optional reads with defaults) -----------------
	//
	// These are type-STRICT on purpose, and that strictness is the whole reason a provider must use
	// them rather than TryGetStringField / TryGetBoolField directly. JBool accepts a JSON boolean,
	// the numbers 0 and 1, and the strings true/yes/on/1 and false/no/off/0 - and records a parameter
	// type violation for anything else instead of silently answering the default. FString::ToBool()
	// returns false for "banana", which is how {"confirm":"banana"} once meant "the caller did not
	// confirm".
	MIFBRIDGE_API FString JStr(const TSharedRef<FJsonObject>& In, const TCHAR* Field, const FString& Default = FString());
	MIFBRIDGE_API double JNum(const TSharedRef<FJsonObject>& In, const TCHAR* Field, double Default = 0.0);
	MIFBRIDGE_API int32 JInt(const TSharedRef<FJsonObject>& In, const TCHAR* Field, int32 Default = 0);
	MIFBRIDGE_API bool JBool(const TSharedRef<FJsonObject>& In, const TCHAR* Field, bool Default = false);

	/** First non-empty of several accepted spellings - lets an endpoint accept {"node"} and
	 *  {"nodeGuid"} interchangeably instead of silently reading nothing. */
	MIFBRIDGE_API FString JStrAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, const FString& Default = FString());
	/** As JBool, but tries several accepted spellings before falling back to Default. Routes through
	 *  JBool so the spelling-tolerant form inherits the same type strictness - two implementations of
	 *  "what counts as a boolean" is exactly the drift this header exists to end. */
	MIFBRIDGE_API bool JBoolAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, bool Default = false);
	/** As JInt, but tries several accepted spellings. Routes through JInt for the same reason. */
	MIFBRIDGE_API int32 JIntAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields, int32 Default = 0);
	/** True if ANY of the spellings is present regardless of value - distinguishes "caller explicitly
	 *  passed false" from "caller omitted the field". Pair it with JBool when a default of true would
	 *  otherwise make an explicit false unreachable. */
	MIFBRIDGE_API bool JHasAny(const TSharedRef<FJsonObject>& In, std::initializer_list<const TCHAR*> Fields);

	// --- The accepted-key guard ----------------------------------------------
	/** Fails Out (and returns true) naming EVERY key in In that is not accepted, and listing the
	 *  accepted set. An IGNORED parameter is worse than a rejected one - the caller gets ok:true and
	 *  debugs the wrong subsystem. Matching is case-INSENSITIVE, mirroring how the accessors above
	 *  find fields, so a key that WOULD be honoured is never rejected. KeyNotes explains a specific
	 *  unknown key where "unrecognised" alone would mislead - an unimplemented capability rather than
	 *  a typo.
	 *
	 *  It tolerates 'op' centrally, because H_batch passes each op object to the handler VERBATIM
	 *  with its routing key included. A provider reimplementing this guard has to remember that or
	 *  every one of its endpoints breaks the moment it is called inside batch - which is a regression
	 *  this bridge already had, fixed once, and should not hand to each provider to rediscover. */
	MIFBRIDGE_API bool RejectUnknownParams(const TSharedRef<FJsonObject>& In, const TSharedRef<FJsonObject>& Out,
		std::initializer_list<const TCHAR*> AcceptedKeys, const TCHAR* AcceptedSummary,
		std::initializer_list<TPair<const TCHAR*, const TCHAR*>> KeyNotes = {});

	// --- Deferral -------------------------------------------------------------
	/** Schedule Work for the next tick with the unattended-script guard RE-ARMED INSIDE the lambda.
	 *
	 *  Use this instead of GEditor->GetTimerManager()->SetTimerForNextTick directly. A TGuardValue
	 *  restores on scope exit, so a handler's guard is already unwound by the time deferred work
	 *  runs - and a modal dialog raised there stops the bridge answering anything at all while the
	 *  editor still looks alive, which is worse than a crash because nothing looks wrong. */
	MIFBRIDGE_API void MifDeferToNextTick(TFunction<void()> Work);
}
