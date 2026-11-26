import type { DeviceView, View } from "../../api/types";
import { Command } from "../Command";
import { ViewFrame } from "../ViewFrame";

interface Props {
  devices: View<DeviceView[]> | undefined;
  now: Date;
}

/**
 * Config on the devices that intent does not have, across the fabric (010
 * FR-011), with each device's `snapl remove` command to copy. The dashboard
 * never runs it (spec 013 FR-016).
 */
export function Undesired({ devices, now }: Props) {
  const found = (devices?.data ?? []).filter((device) => (device.undesired_paths ?? []).length > 0);
  return (
    <ViewFrame title="Undesired config" view={devices} now={now}>
      <div className="text-body">
        {found.length === 0 ? (
          <p className="text-muted">No undesired config on any device.</p>
        ) : (
          <div className="space-y-4">
            {found.map((device) => (
              <div key={device.device_id} role="group" aria-label={device.name}>
                <p className="font-mono text-meta font-semibold">{device.name}</p>
                {device.remove_command && (
                  <p className="mt-2">
                    <Command text={device.remove_command} />
                  </p>
                )}
                <ul className="mt-2 space-y-2">
                  {(device.undesired_paths ?? []).map((path) => (
                    <li key={path} className="chip rounded bg-sunken px-2 py-1 font-mono text-meta break-all">
                      {path}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        <p className="mt-4 max-w-prose text-meta text-muted">
          Reconcile never removes it. An operator previews and removes it with the command above; it is never removed
          on a schedule.
        </p>
      </div>
    </ViewFrame>
  );
}
