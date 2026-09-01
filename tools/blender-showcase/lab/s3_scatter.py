"""STAGE 3 - the procedural half: a geometry-node debris scatter with a live Density slider.

THIS IS THE STAGE THE BENCHMARK WAS REALLY ASKING ABOUT. Everything before it is modelling, which
any bridge can do given enough calls. Authoring a node TREE - creating the group, adding nodes,
wiring them, exposing an input, then driving that input from the modifier - is the part that had no
typed op at all until today, and it is what makes the scatter a knob rather than a one-off bake.

The tree: Group Input geometry -> DistributePointsOnFaces -> InstanceOnPoints (with a debris chunk
as the instance) -> Group Output, with Density and Scale exposed so the room can be made cleaner or
filthier from one number.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage import call, begin, done, box, paint, mat, look

GROUP = "MifDebrisScatter"


def build():
    begin("STAGE 3  procedural debris - a geometry-node tree with a Density slider")
    mat("Rubble", (0.16, 0.15, 0.13), roughness=0.95)

    # The thing being scattered. A real object, so it can be re-skinned or swapped later - which is
    # the advantage of instancing over generating geometry inside the tree.
    chunk = box("Debris_Chunk", -0.07, 0.07, -0.05, 0.05, 0.0, 0.05)
    paint(chunk, "Rubble")
    call("transform_object", {"object": chunk, "location": {"x": -6.0, "y": -6.0, "z": 0.0}})

    # The surface it scatters over - a plane covering the floor, kept separate from Floor so the
    # slab itself is not modified and can still be selected cleanly.
    surf = box("Debris_Surface", 0.2, 17.8, 0.2, 10.8, 0.004, 0.006)
    paint(surf, "Grime")

    look((15.20, 8.80, 1.90), (5.00, 3.00, 0.20))
    call("create_node_group", {"name": GROUP})
    call("add_group_node", {"group": GROUP, "type": "GeometryNodeDistributePointsOnFaces",
                            "name": "Scatter", "location": {"x": -160, "y": 0}})
    call("add_group_node", {"group": GROUP, "type": "GeometryNodeObjectInfo",
                            "name": "ChunkInfo", "location": {"x": -160, "y": -220}})
    call("add_group_node", {"group": GROUP, "type": "GeometryNodeInstanceOnPoints",
                            "name": "Instance", "location": {"x": 120, "y": 0}})

    # EXPOSED INPUTS ARE THE POINT. Without these the tree is a fixed effect; with them it is a
    # control the person watching can drag.
    call("add_group_interface", {"group": GROUP, "name": "Density",
                                 "socketType": "NodeSocketFloat", "default": 18.0,
                                 "min": 0.0, "max": 400.0})
    call("add_group_interface", {"group": GROUP, "name": "Scale",
                                 "socketType": "NodeSocketFloat", "default": 1.0,
                                 "min": 0.0, "max": 6.0})

    call("link_group_nodes", {"group": GROUP, "fromNode": "Group Input", "toNode": "Scatter",
                              "fromSocket": "Geometry", "toSocket": "Mesh"})
    call("link_group_nodes", {"group": GROUP, "fromNode": "Group Input", "toNode": "Scatter",
                              "fromSocket": "Density", "toSocket": "Density"})
    call("link_group_nodes", {"group": GROUP, "fromNode": "Scatter", "toNode": "Instance",
                              "fromSocket": "Points", "toSocket": "Points"})
    call("link_group_nodes", {"group": GROUP, "fromNode": "Scatter", "toNode": "Instance",
                              "fromSocket": "Rotation", "toSocket": "Rotation"})
    call("link_group_nodes", {"group": GROUP, "fromNode": "ChunkInfo", "toNode": "Instance",
                              "fromSocket": "Geometry", "toSocket": "Instance"})
    call("link_group_nodes", {"group": GROUP, "fromNode": "Group Input", "toNode": "Instance",
                              "fromSocket": "Scale", "toSocket": "Scale"})
    call("link_group_nodes", {"group": GROUP, "fromNode": "Instance", "toNode": "Group Output",
                              "fromSocket": "Instances", "toSocket": "Geometry"})

    # READ THE TREE BACK before assigning it. outputReachable is the check that matters: an
    # unconnected Group Output is not an error in Blender, it just passes geometry through, which
    # looks exactly like a modifier that is not working.
    info = call("list_group_nodes", {"group": GROUP})
    print("  tree: %d nodes, %d links, outputReachable=%s"
          % (info.get("nodeCount"), info.get("linkCount"), info.get("outputReachable")))
    if not info.get("outputReachable"):
        raise RuntimeError("the node tree's output is not connected - it would do nothing. %s"
                           % info.get("reachabilityNote"))

    a = call("assign_node_group", {"object": surf, "group": GROUP,
                                   "inputs": {"Density": 22.0, "Scale": 1.4}})
    print("  modifier: %s, inputs applied %s, refused %s"
          % (a.get("modifier"), a.get("inputsApplied"), a.get("inputsRefused") or "{}"))

    # The chunk itself is set to instance-only by parking it far outside the room rather than
    # deleting it - ObjectInfo needs the object to exist, and a deleted instance source silently
    # scatters nothing.
    done("scatter driven by Density/Scale on the modifier; the source chunk is parked at -6,-6")


if __name__ == "__main__":
    build()
