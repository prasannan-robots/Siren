import json
import time
import ssl
import paho.mqtt.client as mqtt


class ViolationPublisher:
    """Publishes driving violation tickets to HiveMQ MQTT broker."""

    TOPIC = "siren/violations"

    def __init__(self, broker, port, username, password):
        self.broker = broker
        self.port = int(port)
        self.client = mqtt.Client(
            client_id=f"siren-dashcam-{int(time.time())}",
            protocol=mqtt.MQTTv5
        )
        self.client.username_pw_set(username, password)
        self.connected = False

        # TLS for HiveMQ Cloud (port 8883)
        if self.port == 8883:
            self.client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect(broker, self.port, keepalive=60)
            self.client.loop_start()
            print(f"[MQTT] Connecting to {broker}:{port}...")
        except Exception as e:
            print(f"[MQTT] Connection failed: {e}")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.connected = True
            print("[MQTT] Connected to broker")
        else:
            print(f"[MQTT] Connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self.connected = False
        print(f"[MQTT] Disconnected (rc={rc})")

    def publish_violation(self, violation_type, coordinates="", street="",
                          speed=None, speed_limit=None, **extra):
        """
        Publish a violation ticket as JSON.

        violation_type: 'speeding', 'ran_stop_sign', 'ran_red_light', 'illegal_right_turn'
        """
        payload = {
            "type": violation_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "coordinates": coordinates,
            "street": street,
        }
        if speed is not None:
            payload["speed"] = speed
        if speed_limit is not None:
            payload["speed_limit"] = speed_limit
        payload.update(extra)

        message = json.dumps(payload)
        result = self.client.publish(self.TOPIC, message, qos=1)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"[MQTT] Violation published: {violation_type} at {payload['coordinates']}")
        else:
            print(f"[MQTT] Failed to publish: {violation_type} (rc={result.rc})")

        return payload

    def disconnect(self):
        """Gracefully disconnect from broker."""
        self.client.loop_stop()
        self.client.disconnect()
        print("[MQTT] Disconnected")
