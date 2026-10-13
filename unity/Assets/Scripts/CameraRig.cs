// CameraRig.cs — 3D office camera framing (t_9af2fada, spec docs/office3d-composition.md)
// Fixed cinematic framing + drag-to-orbit. Orbit target: "RoomCenter" transform if present,
// else (4.8, 1.0, 4.2). Clamps: azimuth +/-50, elevation 15..65, distance 7..16. No pan.
using UnityEngine;

[DisallowMultipleComponent]
public class CameraRig : MonoBehaviour
{
    public Vector3 defaultTarget = new Vector3(4.8f, 1.0f, 4.2f);
    public float defaultDistance = 11.5f;
    public float defaultElevationDeg = 33f;   // ~camera (4.8, 7.2, 13.8)
    public float defaultAzimuthDeg = 0f;      // from +Z (camera side)
    public float fov = 40f;

    public float minAzimuthDeg = -50f, maxAzimuthDeg = 50f;
    public float minElevationDeg = 15f, maxElevationDeg = 65f;
    public float minDistance = 7f, maxDistance = 16f;

    public float dragSensitivityDegPerPx = 0.35f;
    public float zoomStep = 1.08f;
    public float inertiaDamping = 0.90f;

    Vector3 target;
    float azimuth, elevation, distance;
    float velAz = 0f, velEl = 0f;
    bool dragging;

    void Awake()
    {
        var cam = GetComponent<Camera>();
        if (cam != null) cam.fieldOfView = fov;
        var rc = GameObject.Find("RoomCenter");
        if (rc != null) target = rc.transform.position; else target = defaultTarget;
        azimuth = defaultAzimuthDeg;
        elevation = defaultElevationDeg;
        distance = defaultDistance;
    }

    void OnEnable() { ResetToDefault(); }

    public void ResetToDefault()
    {
        azimuth = defaultAzimuthDeg;
        elevation = defaultElevationDeg;
        distance = defaultDistance;
        velAz = velEl = 0f;
    }

    void Update()
    {
        // double-click / double-tap: reset to cinematic pose
        if (Input.GetMouseButtonDown(0) && Input.GetMouseButton(0) &&
            Time.time - lastClick < 0.25f) { ResetToDefault(); lastClick = -1f; }
        else if (Input.GetMouseButtonDown(0)) lastClick = Time.time;

        if (Input.GetMouseButton(0))
        {
            dragging = true;
            float dx = Input.GetAxis("Mouse X");
            float dy = Input.GetAxis("Mouse Y");
            azimuth += dx * dragSensitivityDegPerPx;
            elevation -= dy * dragSensitivityDegPerPx;
            velAz = dx * dragSensitivityDegPerPx;
            velEl = -dy * dragSensitivityDegPerPx;
        }
        else if (draggingInertia())
        {
            azimuth += velAz;
            elevation += velEl;
            velAz *= inertiaDamping;
            velEl *= inertiaDamping;
            if (Mathf.Abs(velAz) < 0.005f && Mathf.Abs(velEl) < 0.005f) { velAz = velEl = 0f; }
        }

        float zoom = Input.GetAxis("Mouse ScrollWheel");
        if (zoom != 0f) distance = Mathf.Clamp(distance * Mathf.Pow(zoomStep, -zoom * 3f), minDistance, maxDistance);

        // touch: 1-finger orbit, 2-finger pinch zoom (WebGL mobile)
        if (Input.touchCount == 1)
        {
            var t = Input.GetTouch(0);
            if (t.phase == TouchPhase.Moved)
            {
                azimuth += t.deltaPosition.x * dragSensitivityDegPerPx;
                elevation -= t.deltaPosition.y * dragSensitivityDegPerPx;
            }
            lastPinchDist = -1f;
        }
        else if (Input.touchCount == 2)
        {
            var t0 = Input.GetTouch(0);
            var t1 = Input.GetTouch(1);
            float dNow = Vector2.Distance(t0.position, t1.position);
            if (lastPinchDist > 1f) distance = Mathf.Clamp(distance * lastPinchDist / dNow, minDistance, maxDistance);
            lastPinchDist = dNow;
        }
        else lastPinchDist = -1f;

        azimuth = Mathf.Clamp(azimuth, minAzimuthDeg, maxAzimuthDeg);
        elevation = Mathf.Clamp(elevation, minElevationDeg, maxElevationDeg);
        distance = Mathf.Clamp(distance, minDistance, maxDistance);

        // spherical around target, azimuth from +Z
        float el = elevation * Mathf.Deg2Rad;
        float az = azimuth * Mathf.Deg2Rad;
        transform.position = target + new Vector3(
            Mathf.Sin(az) * Mathf.Cos(el),
            Mathf.Sin(el),
            Mathf.Cos(az) * Mathf.Cos(el)) * distance;
        transform.LookAt(target);
    }

    bool draggingInertia()
    {
        if (Input.GetMouseButton(0)) return false;
        return velAz != 0f || velEl != 0f;
    }

    float lastClick = -1f;
    float lastPinchDist = -1f;
}
