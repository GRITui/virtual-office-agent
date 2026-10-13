// OfficeSceneSetup.cs — composition drop-in for the 3D office scene (t_9af2fada).
// Editor menu: Office3D/Apply Composition (camera + day/night + HUD).
// Idempotent + additive: applies the ux-ui composition layer (CameraRig, DayNightCycle,
// OfficeHUD, RoomCenter) to the OPEN scene without touching SE/pixel scene content.
// Run after the SE scene (t_34b001b1) lands, then save the scene.
using UnityEngine;
using UnityEditor;

public static class OfficeSceneSetup
{
    [MenuItem("Office3D/Apply Composition (camera + day/night + HUD)")]
    public static void ApplyComposition()
    {
        // 1. RoomCenter (orbit target, spec section 2)
        if (GameObject.Find("RoomCenter") == null)
        {
            var rc = new GameObject("RoomCenter");
            rc.transform.position = new Vector3(4.8f, 1.0f, 4.2f);
            Debug.Log("[Office3D] created RoomCenter at (4.8, 1.0, 4.2)");
        }

        // 2. CameraRig on the main camera
        var cam = Camera.main;
        if (cam == null)
        {
            var camGo = new GameObject("MainCamera");
            camGo.tag = "MainCamera";
            cam = camGo.AddComponent<Camera>();
            Debug.Log("[Office3D] no main camera — created MainCamera");
        }
        if (cam.GetComponent<CameraRig>() == null)
        {
            cam.gameObject.AddComponent<CameraRig>();
            Debug.Log("[Office3D] added CameraRig to " + cam.gameObject.name);
        }

        // 3. DayNightCycle rig
        if (GameObject.Find("DayNightRig") == null)
        {
            var rig = new GameObject("DayNightRig");
            var dn = rig.AddComponent<DayNightCycle>();
            dn.targetCamera = cam;
            Debug.Log("[Office3D] created DayNightRig (DayNightCycle)");
        }

        // 4. OfficeHUD (labels + board counts + footer + drill panel)
        if (GameObject.Find("OfficeHUD") == null)
        {
            var hud = new GameObject("OfficeHUD");
            hud.AddComponent<OfficeHUD>();
            Debug.Log("[Office3D] created OfficeHUD");
        }

        Debug.Log("[Office3D] composition applied — save the scene (Ctrl/Cmd+S) to persist");
    }
}
