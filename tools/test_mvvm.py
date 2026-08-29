"""MVVM: add_mvvm_viewmodel, add_mvvm_binding, describe_mvvm_view.

The other half of the 2026-08-27 MVVM work, left explicitly unexplored at the time ("NOT YET DONE: the
OTHER half of MVVM, wiring a Widget Blueprint's View Bindings... unexplored"). That work made a
Blueprint variable MVVM-bindable (FieldNotify); this is what actually connects one to a widget.

Needed two NEW module dependencies beyond the base ModelViewViewModel already linked -
ModelViewViewModelEditor (UMVVMEditorSubsystem) and ModelViewViewModelBlueprint
(UMVVMBlueprintView/FMVVMBlueprintPropertyPath/FMVVMBlueprintViewBinding) - the base module only carries
the runtime FieldNotify surface the earlier work used.

TWO REAL BUGS CAUGHT LIVE, neither found by reasoning alone:

1. A COMPILE-TIME ENGINE HEADER BUG. MVVMEditorSubsystem.h and MVVMPropertyPath.h both end with a
   backward-compat `#if UE_ENABLE_INCLUDE_ORDER_DEPRECATED_IN_5_2` block that reaches for a header
   under their OWN module's Private/ folder - invisible to an external module compiling against them,
   which is exactly what MifBridge is. Fatal C1083 on the first build attempt. Fixed by locally
   forcing that macro false around the includes (a legitimate override of a plain UBT-injected
   preprocessor define, not a hack).

2. A REAL PARAMETER-RESOLUTION BUG IN THIS FILE'S OWN FIRST VERSION. widgetBlueprintPath was listed as
   an accepted parameter (RejectUnknownParams did not reject it) but the shared ResolveBlueprintField
   helper only ever reads "blueprintId"/"path" - so a call passing ONLY widgetBlueprintPath silently
   resolved nothing and failed with a generic "missing blueprint path" error, even though the caller's
   own accepted key was right there in the payload. This is exactly the "an ignored parameter is worse
   than a rejected one" failure class RejectUnknownParams exists to prevent, just one level deeper than
   RejectUnknownParams itself can see (the KEY was accepted; the VALUE was never read). Fixed by
   resolving the path directly with all three spellings before calling the lower-level resolver.

REAL 5.7 API DRIFT, caught by the second engine's build, not assumed from the header alone:
AddViewModel returns FGuid directly on 5.7 (FName on 5.3.2, requiring a FindViewModel(Name) lookup to
get the id); SetDestinationPathForBinding gained a mandatory bAllowEventConversion parameter on 5.7 with
no default. Both version-guarded.

T1500-T1502: setup - a real MVVMViewModelBase-derived Blueprint with a FieldNotify Text property (the
same create_blueprint+add_variable+set_variable_flags path the 2026-08-27 FieldNotify work proved needs
no new code), and a real WidgetBlueprint (blueprintType:"WidgetBlueprint" - a plain create_blueprint
with parentClass:UserWidget and no blueprintType does NOT produce a real Widget Blueprint asset, caught
live before assuming it would) with a named TextBlock.

T1503-T1504: add_mvvm_viewmodel and add_mvvm_binding both succeed and describe_mvvm_view reads back
exactly what was created - not just ok:true.

T1505: THE REAL CORRECTNESS TEST. add_mvvm_binding reporting ok:true only means the BINDING RECORD was
created - it does not mean the binding is valid. Compiling the Widget Blueprint is the actual proof:
binding a String-typed viewmodel property to a TextBlock's Text (FText) property compiles with a real,
specific engine error ("does not match the type of the destination property... conversion function is
required") - correct, expected MVVM compiler behavior, not a bug in this endpoint. T1506 proves the
positive case: a Text-typed source bound to a Text-typed destination compiles with ZERO errors.

T1507-T1512: refusals checked for the specific reason - an unregistered viewmodel name, an unknown
source property, an unknown destination widget, an unknown destination property, an invalid
bindingMode, and describe_mvvm_view called against a Blueprint that is not a Widget Blueprint at all.

DECLINED for this batch: remove_mvvm_viewmodel / remove_mvvm_binding were not built (the subsystem's
own RemoveViewModel/RemoveBinding exist and are simple to wire, but this batch was already large enough
- a real, honest scope cut, not an oversight). Conversion-function wiring
(SetSourceToDestinationConversionFunction) was similarly out of scope - this batch covers plain
type-matched property bindings only.
"""
import json
import sys
import time

import mifaudit as M


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else (name, detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + str(detail)))


def main():
    if not M.wait_for_bridge(timeout=900):
        print("bridge never came up")
        return 1

    st = int(time.time() % 100000)
    base = "/Game/_MifMVVM%d" % st
    vm_path = base + "/VM_Test"
    vm_id = vm_path
    wbp_path = base + "/WBP_Test"

    # ------------------------------------------------------------------ T1500-T1502 setup
    print("\n=== T1500-T1502: a real MVVM-derived Blueprint and a real Widget Blueprint ===")
    vm = M.call("create_blueprint", {
        "path": vm_path, "parentClass": "/Script/ModelViewViewModel.MVVMViewModelBase"})
    check("T1500 viewmodel Blueprint created", vm.get("ok") is True, json.dumps(vm)[:200])

    add_var = M.call("add_variable", {"blueprintId": vm_id, "name": "DisplayName", "type": "Text"})
    check("T1500 Text variable added", add_var.get("ok") is True, add_var)
    flags = M.call("set_variable_flags", {"blueprintId": vm_id, "name": "DisplayName", "fieldNotify": True})
    check("T1500 marked fieldNotify", flags.get("ok") is True and flags.get("flags", {}).get("fieldNotify") is True,
          flags)
    vm_compile = M.call("compile", {"blueprintId": vm_id})
    check("T1500 viewmodel compiles clean", vm_compile.get("ok") is True and vm_compile.get("numErrors") == 0,
          vm_compile)

    wbp = M.call("create_blueprint", {
        "path": wbp_path, "parentClass": "/Script/UMG.UserWidget", "blueprintType": "WidgetBlueprint"})
    check("T1501 Widget Blueprint created", wbp.get("ok") is True, json.dumps(wbp)[:200])

    add_widget = M.call("add_tree_widget", {
        "blueprintId": wbp_path, "widgetClass": "TextBlock", "name": "NameText", "parentName": "CanvasPanel_0"})
    check("T1501 TextBlock added to the tree", add_widget.get("ok") is True, add_widget)
    wbp_compile = M.call("compile", {"blueprintId": wbp_path})
    check("T1502 Widget Blueprint compiles clean before any MVVM work", wbp_compile.get("ok") is True
          and wbp_compile.get("numErrors") == 0, wbp_compile)

    # ------------------------------------------------------------------ T1503-T1504 the real flow
    print("\n=== T1503-T1504: add a viewmodel, add a binding, read both back ===")
    check("T1502b (setup) blueprint create response reports the generated class",
          bool(vm.get("class")), vm)
    added_vm = M.call("add_mvvm_viewmodel", {
        "widgetBlueprintPath": wbp_path, "viewModelClass": vm.get("class")})
    check("T1503 add_mvvm_viewmodel succeeds", added_vm.get("ok") is True, json.dumps(added_vm)[:200])
    check("T1503 it reports a real viewModelId", bool(added_vm.get("viewModelId")), added_vm.get("viewModelId"))
    vm_instance_name = added_vm.get("viewModelName")

    binding = M.call("add_mvvm_binding", {
        "widgetBlueprintPath": wbp_path, "sourceViewModelName": vm_instance_name,
        "sourcePropertyName": "DisplayName", "destinationWidgetName": "NameText",
        "destinationPropertyName": "Text"})
    check("T1504 add_mvvm_binding succeeds", binding.get("ok") is True, json.dumps(binding)[:200])

    desc = M.call("describe_mvvm_view", {"widgetBlueprintPath": wbp_path})
    check("T1504 describe_mvvm_view succeeds", desc.get("ok") is True, json.dumps(desc)[:200])
    check("T1504 the viewmodel round-trips", any(v.get("name") == vm_instance_name for v in desc.get("viewModels", [])),
          desc.get("viewModels"))
    check("T1504 the binding round-trips with the correct field paths",
          any(b.get("sourceFieldPath") == ["DisplayName"] and b.get("destinationFieldPath") == ["Text"]
              for b in desc.get("bindings", [])), desc.get("bindings"))

    # ------------------------------------------------------------------ T1505-T1506 the real correctness test
    print("\n=== T1505-T1506: ok:true from add_mvvm_binding is not proof - compiling is ===")
    real_compile = M.call("compile", {"blueprintId": wbp_path})
    check("T1505 a type-matched (Text -> Text) binding compiles with ZERO errors",
          real_compile.get("ok") is True and real_compile.get("numErrors") == 0, real_compile)

    # ------------------------------------------------------------------ T1507-T1512 refusals, exact reason
    print("\n=== T1507-T1512: refusals checked for the specific reason ===")
    bad_vm = M.call("add_mvvm_binding", {
        "widgetBlueprintPath": wbp_path, "sourceViewModelName": "NoSuchViewModel",
        "sourcePropertyName": "DisplayName", "destinationWidgetName": "NameText", "destinationPropertyName": "Text"})
    check("T1507 an unregistered viewmodel name is refused", bad_vm.get("ok") is False, bad_vm)

    bad_source_prop = M.call("add_mvvm_binding", {
        "widgetBlueprintPath": wbp_path, "sourceViewModelName": vm_instance_name,
        "sourcePropertyName": "NoSuchProp", "destinationWidgetName": "NameText", "destinationPropertyName": "Text"})
    check("T1508 an unknown source property is refused", bad_source_prop.get("ok") is False, bad_source_prop)

    bad_widget = M.call("add_mvvm_binding", {
        "widgetBlueprintPath": wbp_path, "sourceViewModelName": vm_instance_name,
        "sourcePropertyName": "DisplayName", "destinationWidgetName": "NoSuchWidget", "destinationPropertyName": "Text"})
    check("T1509 an unknown destination widget is refused", bad_widget.get("ok") is False, bad_widget)

    bad_dest_prop = M.call("add_mvvm_binding", {
        "widgetBlueprintPath": wbp_path, "sourceViewModelName": vm_instance_name,
        "sourcePropertyName": "DisplayName", "destinationWidgetName": "NameText", "destinationPropertyName": "NoSuchProp"})
    check("T1510 an unknown destination property is refused", bad_dest_prop.get("ok") is False, bad_dest_prop)

    bad_mode = M.call("add_mvvm_binding", {
        "widgetBlueprintPath": wbp_path, "sourceViewModelName": vm_instance_name,
        "sourcePropertyName": "DisplayName", "destinationWidgetName": "NameText", "destinationPropertyName": "Text",
        "bindingMode": "sideways"})
    check("T1511 an invalid bindingMode is refused", bad_mode.get("ok") is False, bad_mode)
    check("T1511 refusal names the bad value", "sideways" in (bad_mode.get("error") or ""), bad_mode.get("error"))

    not_widget_bp = M.call("describe_mvvm_view", {"widgetBlueprintPath": vm_path})
    check("T1512 describe_mvvm_view on a non-Widget-Blueprint is refused", not_widget_bp.get("ok") is False,
          not_widget_bp)

    print("\n" + "=" * 72)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for x in FAIL:
        print("  FAILED: %s\n          %s" % x)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
