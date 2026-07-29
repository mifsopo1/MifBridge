// MifBridge — external endpoint registration for provider plugins.
//
// MifBridge's built-in endpoints are MIF_DECL'd in Private/MifBridgeHandlers.h and MIF_BIND'd into
// the function-local static map in Private/MifBridgeCommon.cpp (191 built-ins live; 12 external =
// 203 endpoints total). Line numbers are deliberately NOT cited here any more: every one of the
// seven this header used to carry had drifted, and a wrong citation is the MECHANISM of the
// duplicate-helper bug class — the next reader jumps to the cited line, finds nothing, and writes a
// local copy. Grep the symbol instead; `self_audit` reports the live counts. Providers
// (MifKismetReconstructor and any future Mif* plugin) instead register named handlers HERE at their
// own module startup. The endpoint exists only while its provider is installed; self_audit names the
// provider per endpoint (endpointDetails[].provider / externalProviders[]).
//
// This is the generalisation of the delegate pattern that already works between the two plugins
// (CompiledBlueprintReconstructor.h's KISMET_API delegate accessors). It keeps MifBridge free of any
// dependency on its providers: MifBridge loads and serves its built-ins whether or not a provider is
// present — the soft-coupling property documented at Private/MifBridgeReconstruct.cpp:73-74.
//
// The MIF_DECL/MIF_BIND invariant is UNAFFECTED: external endpoints never appear in either file, so
// `grep -c "MIF_DECL(" MifBridgeHandlers.h` == `grep -c "MIF_BIND(" MifBridgeCommon.cpp` still holds.
// The registry contract is three-way:
//   built-ins = MIF_DECL + MIF_BIND (+ @mcp.tool in server.py)
//   externals = ONE RegisterExternalEndpoint call in the provider (+ @mcp.tool in server.py)
// and self_audit.endpoints — built from the LIVE merged map — remains the single source of truth.
//
// HARD RULE — the registration API must never touch module-startup state.
// Providers load EARLIER than MifBridge (MifKismetReconstructor.uplugin:17 = "Default";
// MifBridge.uplugin:17 = "PostEngineInit"), and linking against MifBridge makes the OS loader map the
// MifBridge DLL when the PROVIDER DLL loads — so RegisterExternalEndpoint is legally called BEFORE
// FMifBridgeModule::StartupModule has run at all. The registry is therefore a function-local static
// (initialise-on-first-use), and nothing reachable from here may read the server, the routes, the
// menus or the token. Adding such a read would turn a working provider into a startup crash.
//
// Registration must also precede route binding: routes are bound ONCE per name from
// GetEndpointNames() in FMifBridgeServer::Start(). Late registration is refused loudly rather than
// being silently invisible.
//
// External endpoints are reachable from `batch` as well as directly: H_batch mirrors RunEndpoint's
// resolution order (built-ins, then externals) via FindExternalHandler. Before that they answered
// "unknown op" from inside ops[] while self_audit listed them as present.
#pragma once

#include "CoreMinimal.h"

class FJsonObject;   // NOT an include: MifBridge lists "Json" as a PRIVATE dependency
                     // (MifBridge.Build.cs:39), and a UBT module's private dependencies do not
                     // propagate their include paths to dependents — so a PUBLIC MifBridge header
                     // must never pull in Dom/JsonObject.h. A TSharedRef<FJsonObject> appearing only
                     // as a TFunction<> parameter type needs a declaration, not a definition.
                     // (Providers already depend on Json themselves and include it in their .cpp.)

namespace MifBridge
{
	// Same shape as the internal FHandlerFn (Private/MifBridgeHandlers.h). Game thread only —
	// dispatch hops there in FMifBridgeServer::HandleHttp before calling RunEndpoint. The
	// unrecognised-parameter-is-an-error contract (RejectUnknownParams, Private/MifBridgeHandlers.h)
	// applies to external handlers identically; that helper is private to MifBridge, so a provider
	// implements the equivalent guard locally.
	using FExternalHandler = TFunction<void(const TSharedRef<FJsonObject>& /*In*/, const TSharedRef<FJsonObject>& /*Out*/)>;

	// ONE bucket per endpoint, BY CONSTRUCTION. The twin-set contradiction class that self_audit
	// polices for built-ins (policyContradictions — an endpoint listed in BOTH literal TSets) is
	// unrepresentable here: a descriptor carries a single enum.
	//   ReadOnly    — no blanket transaction (else every call pushes an empty undo entry)
	//   SelfManaged — runs a full CompileBlueprint / asset save; opens its OWN tight transactions.
	//                 Also makes IsCompileHeavyEndpoint true (it derives from IsSelfManagedEndpoint),
	//                 which keeps the endpoint out of batch's single open transaction — reinstancing
	//                 captured by an undo step is a dead CDO. One deliberate subtraction exists on the
	//                 built-in side (set_property, whose compile lives in one branch only); externals
	//                 have no such carve-out.
	//   Transacted  — RunEndpoint wraps the call in one FScopedTransaction, so Ctrl-Z undoes the whole
	//                 bridge action — and CANCELS it when the handler answers ok:false, so a handler
	//                 that mutates and then fails is atomic rather than leaving a partial edit.
	enum class EEndpointBucket : uint8 { ReadOnly, SelfManaged, Transacted };

	struct FExternalEndpointDesc
	{
		FString Name;                                    // lowercase snake_case, verb_noun
		EEndpointBucket Bucket = EEndpointBucket::Transacted;
		FString Provider;                                // e.g. "MifKismetReconstructor" — surfaced by self_audit
		FString Summary;                                 // one-liner for self_audit / docs
		FExternalHandler Handler;
	};

	/** Register from your module's StartupModule. Returns false + OutError on: name collision with a
	 *  built-in or another external, empty name, empty handler, empty Provider, non-game-thread call,
	 *  or registration after the HTTP route table is live. A false return means the endpoint does NOT
	 *  exist — log it as an error; never assume registration succeeded. */
	MIFBRIDGE_API bool RegisterExternalEndpoint(FExternalEndpointDesc Desc, FString* OutError = nullptr);

	/** Module shutdown symmetry (the reconstructor already unbinds all three engine delegates at
	 *  MifKismetReconstructorModule.cpp:51-54). Returns the number removed. Note the HTTP route for a
	 *  removed name stays bound until the server restarts; RunEndpoint then answers
	 *  "unknown endpoint", which is the correct answer once the provider is gone. */
	MIFBRIDGE_API int32 UnregisterExternalEndpoints(const FString& Provider);
}
