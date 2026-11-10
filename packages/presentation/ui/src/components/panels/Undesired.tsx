import type { DeviceView, View } from "../../api/types";
import { ViewFrame } from "../ViewFrame";

interface Props {
  devices: View<DeviceView[]> | undefined;
  now: Date;
}

/**
 * Config on the devices that intent does not have, across the fabric (010
 * FR-011). No command: removing it is not automatic (#65).
 */
export function Undesired({ devices, now }: Props) {
  const found = (devices?.data ?? []).filter((device) => (device.undesired_paths ?? []).length > 0);
  return (
    <ViewFrame title="Undesired config" view={devices} now={now}>
      <div className="text-sm">
        {found.length === 0 ? (
          <p className="text-muted">No undesired config on any device.</p>
        ) : (
          <div className="space-y-3">
            {found.map((device) => (
              <div key={device.device_id} role="group" aria-label={device.name}>
                <p className="font-mono text-xs font-semibold">{device.name}</p>
                <ul className="mt-1 space-y-1">
                  {(device.undesired_paths ?? []).map((path) => (
                    <li key={path} className="rounded bg-sunken px-2 py-1 font-mono text-xs break-all">
                      {path}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-muted">
          Config on a device but not in intent is not removed automatically (#65).
        </p>
      </div>
    </ViewFrame>
  );
}
