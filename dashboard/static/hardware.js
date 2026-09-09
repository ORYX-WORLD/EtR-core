(() => {
  const root = document.querySelector('[data-hardware-editor]');
  if (!root) return;
  const adc = root.querySelector('[name="ads1263"]');
  const modbus = root.querySelector('[name="modbus"]');
  const port = root.querySelector('[name="serial_port"]');
  const message = root.querySelector('[role="status"]');
  const save = root.querySelector('[data-hardware-save]');
  let profile;
  const load = async () => {
    save.disabled = true;
    try {
      const response = await fetch('/api/hardware', { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Lecture impossible.');
      profile = data.profile;
      adc.checked = profile.ads1263.enabled;
      modbus.checked = profile.modbus.enabled;
      port.value = profile.modbus.serial_port;
      save.disabled = false;
      message.textContent = 'Choix enregistré sur ce Raspberry. Un matériel décoché ne sera pas interrogé.';
    } catch (error) { message.textContent = error.message; }
  };
  root.querySelector('[data-hardware-reload]').addEventListener('click', load);
  save.addEventListener('click', async () => {
    if (!profile) return;
    save.disabled = true;
    try {
      const response = await fetch('/api/hardware', { method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-ETR-Local-Write': '1' },
        body: JSON.stringify({ ...profile, ads1263: { enabled: adc.checked },
          modbus: { enabled: modbus.checked, serial_port: port.value.trim() } }) });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Enregistrement impossible.');
      profile = data.profile;
      message.textContent = 'Enregistré. Application au prochain cycle de lecture. La collecte Modbus reste à valider.';
      window.dispatchEvent(new Event('etr-hardware-profile-saved'));
    } catch (error) { message.textContent = error.message; }
    finally { save.disabled = false; }
  });
  load();
})();
