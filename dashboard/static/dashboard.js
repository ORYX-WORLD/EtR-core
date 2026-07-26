(() => {
  const one = selector => document.querySelector(selector);
  const connection = one('[data-connection]');
  const connectionLabel = one('[data-connection-label]');
  const health = one('[data-health]');
  const fields = {
    installation: one('[data-installation]'), hostname: one('[data-hostname]'), health: one('[data-health-label]'),
    updated: one('[data-last-update]'), cpu: one('[data-cpu]'), memory: one('[data-memory]'), disk: one('[data-disk]'),
    uptime: one('[data-uptime]'), api: one('[data-api-state]'), schema: one('[data-schema]'),
    measurements: one('[data-measurements]'), states: one('[data-states]'), alerts: one('[data-alerts]'),
    telemetryBadge: one('[data-telemetry-badge]'), alertCount: one('[data-alert-count]')
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
      alert.textContent = typeof item === 'string' ? item : String(item.message || item.code || 'Alarme active');
      fields.alerts.append(alert);
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
    renderMap(fields.measurements, telemetry.measurements, 'Aucune mesure instrumentée publiée.');
    renderMap(fields.states, telemetry.states, 'Aucun état métier publié.');
    renderAlerts(telemetry.alerts);
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
