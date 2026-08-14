(() => {
  const one = selector => document.querySelector(selector);
  const connection = one('[data-connection]');
  const connectionLabel = one('[data-connection-label]');
  const health = one('[data-health]');
  const enrollmentPanel = one('[data-enrollment]');
  const enrollmentState = one('[data-enrollment-state]');
  const fields = {
    installation: one('[data-installation]'), hostname: one('[data-hostname]'), health: one('[data-health-label]'),
    updated: one('[data-last-update]'), cpu: one('[data-cpu]'), memory: one('[data-memory]'), disk: one('[data-disk]'),
    uptime: one('[data-uptime]'), api: one('[data-api-state]'), schema: one('[data-schema]'),
    measurements: one('[data-measurements]'), states: one('[data-states]'), alerts: one('[data-alerts]'),
    telemetryBadge: one('[data-telemetry-badge]'), alertCount: one('[data-alert-count]'),
    enrollmentCode: one('[data-enrollment-code]'), enrollmentExpiry: one('[data-enrollment-expiry]'),
    enrollmentInstallation: one('[data-enrollment-installation]'), sensorGrid: one('[data-sensor-grid]'),
    adcBadge: one('[data-adc-badge]')
  };

  const labels = value => String(value).replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
  const formatValue = value => {
    if (typeof value === 'boolean') return value ? 'Oui' : 'Non';
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
    if (value == null || value === '') return '—';
    return String(value);
  };
  const setText = (node, value) => { if (node) node.textContent = value; };
  const setMeter = (selector, value) => { const meter = one(selector); if (meter) meter.value = Math.max(0, Math.min(100, Number(value) || 0)); };
  const uptime = seconds => {
    const total = Math.max(0, Number(seconds) || 0);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return days ? `${days} j ${hours} h` : hours ? `${hours} h ${minutes} min` : `${minutes} min`;
  };
  const remaining = seconds => {
    const total = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return hours ? `${hours} h ${minutes} min` : `${minutes} min`;
  };
  const renderMap = (root, values, emptyText) => {
    if (!root) return;
    root.replaceChildren();
    const entries = Object.entries(values || {});
    if (!entries.length) {
      const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = emptyText; root.append(empty); return;
    }
    for (const [key, value] of entries) {
      const row = document.createElement('div'); row.className = 'data-row';
      const name = document.createElement('span'); name.textContent = labels(key);
      const content = document.createElement('strong'); content.textContent = formatValue(value);
      row.append(name, content); root.append(row);
    }
  };
  const renderAlerts = alerts => {
    if (!fields.alerts) return;
    fields.alerts.replaceChildren();
    const items = Array.isArray(alerts) ? alerts : [];
    setText(fields.alertCount, `${items.length} alarme${items.length > 1 ? 's' : ''}`);
    if (!items.length) {
      const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Aucune alarme active.'; fields.alerts.append(empty); return;
    }
    for (const item of items.slice(0, 20)) {
      const alert = document.createElement('div'); alert.className = 'alert';
      alert.dataset.severity = typeof item === 'object' ? String(item.severity || 'warning') : 'warning';
      alert.textContent = typeof item === 'string' ? item : String(item.message || item.code || 'Alarme active');
      fields.alerts.append(alert);
    }
  };
  const statusLabel = status => ({
    ok: 'Opérationnel',
    signal_low: 'Signal trop bas',
    signal_high: 'Signal trop haut',
    reference_resistor_missing_or_probe_open: 'Lecture brute disponible',
    short_circuit: 'Court-circuit à contrôler',
    curve_required: 'Courbe NTC à valider',
    out_of_range: 'Hors plage',
    offline: 'Hors ligne'
  }[status] || labels(status || 'inconnu'));
  const alarmKey = (id, side) => `etr.sensor.${String(id)}.alarm.${side}`;
  const savedAlarm = (id, side) => {
    const value = localStorage.getItem(alarmKey(id, side));
    return value == null || value === '' ? null : Number(value);
  };
  const alarmEditor = sensor => {
    const root = document.createElement('div'); root.className = 'sensor-alarm-editor';
    const title = document.createElement('strong'); title.textContent = 'Alarmes température';
    const controls = document.createElement('div'); controls.className = 'sensor-alarm-controls';
    for (const [side, label] of [['low', 'Basse'], ['high', 'Haute']]) {
      const field = document.createElement('label'); field.textContent = label;
      const input = document.createElement('input');
      input.type = 'number'; input.step = '0.5'; input.inputMode = 'decimal'; input.placeholder = '—';
      const saved = savedAlarm(sensor.id, side);
      if (Number.isFinite(saved)) input.value = String(saved);
      input.setAttribute('aria-label', `Alarme ${label.toLowerCase()} en degrés Celsius`);
      input.addEventListener('input', () => {
        if (input.value === '') localStorage.removeItem(alarmKey(sensor.id, side));
        else localStorage.setItem(alarmKey(sensor.id, side), String(Number(input.value)));
      });
      const unit = document.createElement('span'); unit.textContent = '°C';
      field.append(input, unit); controls.append(field);
    }
    const note = document.createElement('small');
    note.textContent = sensor.value == null ? 'Seuils enregistrés · en attente d’une température valide' : 'Seuils actifs sur la température mesurée';
    root.append(title, controls, note);
    return root;
  };
  const renderSensors = telemetry => {
    if (!fields.sensorGrid) return [];
    fields.sensorGrid.replaceChildren();
    const sensorAlarms = [];
    const hardware = telemetry.hardware || {};
    const sensors = Array.isArray(telemetry.sensors) ? telemetry.sensors : [];
    const hardwareOnline = hardware.status === 'online';
    if (fields.adcBadge) {
      fields.adcBadge.dataset.status = hardwareOnline ? 'ok' : 'offline';
      fields.adcBadge.textContent = hardwareOnline ? `ADS1263 détecté · ID ${hardware.chip_id ?? '—'}` : 'ADS1263 indisponible';
    }
    if (!sensors.length) {
      const empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = hardwareOnline ? 'Aucun canal configuré.' : 'Aucune lecture : contrôle SPI/GPIO en cours.';
      fields.sensorGrid.append(empty);
      return sensorAlarms;
    }
    for (const sensor of sensors) {
      const article = document.createElement('article');
      article.className = 'sensor-card';
      article.dataset.status = String(sensor.status || 'unknown');

      const head = document.createElement('div'); head.className = 'sensor-card-head';
      const identity = document.createElement('div');
      const channel = document.createElement('span'); channel.className = 'sensor-channel'; channel.textContent = `AIN${sensor.ain}`;
      const title = document.createElement('h3'); title.textContent = String(sensor.label || sensor.id || 'Capteur');
      const model = document.createElement('small'); model.textContent = String(sensor.model || '');
      identity.append(channel, title, model);
      const badge = document.createElement('span'); badge.className = 'sensor-status'; badge.textContent = statusLabel(sensor.status);
      head.append(identity, badge);

      const value = document.createElement('div'); value.className = 'sensor-value';
      const primary = document.createElement('strong');
      const rawProbeReading = sensor.kind === 'temperature' && sensor.value == null && sensor.signal_v != null;
      primary.textContent = rawProbeReading
        ? `${formatValue(sensor.signal_v)} V brute`
        : sensor.value == null ? '—' : `${formatValue(sensor.value)} ${sensor.unit || ''}`.trim();
      const signal = document.createElement('span');
      const details = [];
      if (sensor.signal_v != null && !rawProbeReading) details.push(`${formatValue(sensor.signal_v)} V`);
      if (rawProbeReading) details.push('Température non calculée');
      if (sensor.resistance_ohm != null) details.push(`${formatValue(sensor.resistance_ohm)} Ω`);
      signal.textContent = details.join(' · ') || 'Aucune valeur exploitable';
      value.append(primary, signal);

      const message = document.createElement('p'); message.textContent = String(sensor.message || '');
      const expected = document.createElement('small'); expected.className = 'sensor-expected'; expected.textContent = String(sensor.expected || '');
      article.append(head, value, message, expected);
      if (sensor.kind === 'temperature') {
        article.append(alarmEditor(sensor));
        const low = savedAlarm(sensor.id, 'low');
        const high = savedAlarm(sensor.id, 'high');
        const measured = Number(sensor.value);
        if (sensor.value != null && Number.isFinite(measured) && Number.isFinite(low) && measured < low) {
          article.dataset.alarm = 'active';
          sensorAlarms.push({ severity: 'warning', message: `${sensor.label} : ${formatValue(measured)} °C sous l’alarme basse ${formatValue(low)} °C` });
        }
        if (sensor.value != null && Number.isFinite(measured) && Number.isFinite(high) && measured > high) {
          article.dataset.alarm = 'active';
          sensorAlarms.push({ severity: 'warning', message: `${sensor.label} : ${formatValue(measured)} °C au-dessus de l’alarme haute ${formatValue(high)} °C` });
        }
      }
      fields.sensorGrid.append(article);
    }
    return sensorAlarms;
  };
  const renderEnrollment = enrollment => {
    if (!enrollmentPanel) return;
    if (!enrollment || enrollment.required !== true) {
      enrollmentPanel.hidden = true;
      return;
    }
    enrollmentPanel.hidden = false;
    const code = String(enrollment.activation_code || '').trim();
    const status = String(enrollment.status || 'unconfigured');
    enrollmentState.dataset.enrollmentState = status;
    setText(fields.enrollmentInstallation, enrollment.installation_id ? `Installation : ${enrollment.installation_id}` : 'Identification de l’installation…');
    if (code) {
      setText(fields.enrollmentCode, code);
      if (status === 'expired') setText(fields.enrollmentExpiry, 'Code expiré — un nouveau code va être généré automatiquement.');
      else if (Number.isFinite(Number(enrollment.expires_in_seconds))) setText(fields.enrollmentExpiry, `Valable encore ${remaining(enrollment.expires_in_seconds)}.`);
      else setText(fields.enrollmentExpiry, 'Code temporaire à usage unique.');
    } else {
      setText(fields.enrollmentCode, status === 'expired' ? 'Renouvellement…' : 'Génération…');
      setText(fields.enrollmentExpiry, 'Connexion au service d’activation ORYX en cours.');
    }
  };

  const render = payload => {
    const data = payload.data || {};
    const system = data.system || {};
    const telemetry = data.telemetry || {};
    connection.dataset.connection = payload.api_online ? 'online' : 'offline';
    setText(connectionLabel, payload.api_online ? 'API locale connectée' : 'API locale indisponible');
    setText(fields.api, payload.api_online ? 'connectée' : 'indisponible');
    setText(fields.installation, data.device?.installation_id || 'EtR local');
    setText(fields.hostname, data.device?.hostname ? `Hôte : ${data.device.hostname}` : 'Identité locale non disponible');
    health.dataset.health = data.health || (payload.api_online ? 'ok' : 'degraded');
    setText(fields.health, data.health === 'ok' ? 'Opérationnel' : payload.api_online ? 'Dégradé' : 'Hors liaison');
    setText(fields.updated, telemetry.updated_at ? `Mesures : ${new Date(telemetry.updated_at).toLocaleString('fr-FR')}` : `Actualisation : ${new Date().toLocaleTimeString('fr-FR')}`);
    setText(fields.cpu, `${formatValue(system.cpu_percent)} %`); setMeter('[data-cpu-meter]', system.cpu_percent);
    setText(fields.memory, `${formatValue(system.memory_percent)} %`); setMeter('[data-memory-meter]', system.memory_percent);
    setText(fields.disk, `${formatValue(system.disk_percent)} %`); setMeter('[data-disk-meter]', system.disk_percent);
    setText(fields.uptime, uptime(system.uptime_seconds)); setText(fields.schema, data.schema_version || '—');
    setText(fields.telemetryBadge, telemetry.online ? 'Source connectée' : 'Source en attente');
    renderEnrollment(data.enrollment);
    const sensorAlarms = renderSensors(telemetry) || [];
    renderMap(fields.measurements, telemetry.measurements, 'Aucune mesure instrumentée publiée.');
    renderMap(fields.states, telemetry.states, 'Aucun état métier publié.');
    renderAlerts([...(Array.isArray(telemetry.alerts) ? telemetry.alerts : []), ...sensorAlarms]);
  };

  const refresh = async () => {
    try {
      const response = await fetch('/api/status', { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('status');
      render(await response.json());
    } catch {
      render({ api_online: false, data: {} });
    }
  };
  refresh();
  setInterval(refresh, 5000);
})();
