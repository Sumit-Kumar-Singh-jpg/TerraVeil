(() => {
    const zeroButton = document.getElementById('btn-stage3-zero');
    const seismicButton = document.getElementById('btn-stage3-geophone');
    const statusElement = document.getElementById('stage3-baseline-status');

    if (!zeroButton || !seismicButton || !statusElement) return;

    let telemetry = window.TerraVeilTelemetry || null;
    let alertState = null;
    let alertPollBusy = false;

    function ensureEarlyOverlay() {
        let overlay = document.getElementById('stage3-early-warning');
        if (overlay) return overlay;

        overlay = document.createElement('div');
        overlay.id = 'stage3-early-warning';
        overlay.className = 'stage3-early-warning hidden';
        overlay.innerHTML = `
            <div class="stage3-early-backdrop"></div>
            <section class="stage3-early-card" role="alertdialog" aria-modal="true">
                <div class="stage3-early-kicker">
                    SIMULATED FOUR-GEOPHONE LAYER · DEMO ONLY
                </div>
                <h2>SEISMIC PRECURSOR DETECTED</h2>
                <p id="stage3-early-message"></p>

                <div class="stage3-localization-grid">
                    <div><span>X</span><strong id="stage3-geo-x">—</strong></div>
                    <div><span>Y</span><strong id="stage3-geo-y">—</strong></div>
                    <div><span>Z</span><strong id="stage3-geo-z">—</strong></div>
                    <div><span>T₀</span><strong id="stage3-geo-t">—</strong></div>
                </div>

                <div class="stage3-arrivals" id="stage3-geo-arrivals"></div>

                <div class="stage3-early-note">
                    Early-warning stage only: geophones are simulated in this
                    prototype. UG/LD physical deformation has not yet been required.
                </div>

                <button type="button"
                        id="btn-stage3-early-ack"
                        class="stage3-early-ack">
                    ACKNOWLEDGE EARLY WARNING
                </button>
            </section>
        `;
        document.body.appendChild(overlay);
        overlay
            .querySelector('#btn-stage3-early-ack')
            .addEventListener('click', acknowledgeEarlyWarning);
        return overlay;
    }

    function showEarlyWarning(state) {
        const overlay = ensureEarlyOverlay();
        const event = state?.geophone_event || {};

        overlay.classList.remove('hidden');
        overlay.querySelector('#stage3-early-message').textContent =
            state?.message || 'Simulated microseismic precursor detected.';

        const display = (value, suffix) =>
            Number.isFinite(Number(value))
                ? `${Number(value).toFixed(1)} ${suffix}`
                : '—';

        overlay.querySelector('#stage3-geo-x').textContent = display(event.x_cm, 'cm');
        overlay.querySelector('#stage3-geo-y').textContent = display(event.y_cm, 'cm');
        overlay.querySelector('#stage3-geo-z').textContent = display(event.z_cm, 'cm');
        overlay.querySelector('#stage3-geo-t').textContent =
            event.origin_time
                ? new Date(event.origin_time).toLocaleTimeString()
                : '—';

        const arrivals = event.arrival_ms || {};
        overlay.querySelector('#stage3-geo-arrivals').innerHTML =
            Object.entries(arrivals)
                .map(([station, ms]) => `
                    <div>
                        <span>${station}</span>
                        <strong>${Number(ms).toFixed(1)} ms</strong>
                    </div>
                `)
                .join('');

        window.startSirenSound?.();
    }

    function hideEarlyWarning(stopAudio = true) {
        document
            .getElementById('stage3-early-warning')
            ?.classList.add('hidden');

        if (stopAudio) window.stopSirenSound?.();
    }

    function progress() {
        if (!telemetry || telemetry.mode !== 'REAL') {
            return {
                ready: false,
                disarmed: false,
                text: 'Demo engine available in REAL HARDWARE mode'
            };
        }

        const required = Number(alertState?.baseline_samples_required) || 5;

        if (!alertState?.demo_armed) {
            return {
                ready: false,
                disarmed: true,
                text: 'DISARMED · reset the physical setup, then press SET ZERO / ARM DEMO'
            };
        }

        const nodes = telemetry.nodes || [];
        const readings = telemetry.readings || {};

        if (!nodes.length) {
            return {
                ready: false,
                disarmed: false,
                text: 'ARMING · waiting for registered physical nodes'
            };
        }

        const parts = nodes.map(node => {
            const reading = readings[node.node_id];
            const count = Number(reading?.baseline_samples) || 0;
            const ready = Number(reading?.baseline_ready) === 1;
            return {
                id: node.node_id,
                count,
                ready
            };
        });

        if (parts.every(item => item.ready)) {
            return {
                ready: true,
                disarmed: false,
                text: 'ARMED · baseline locked · early-warning and collapse stages ready'
            };
        }

        return {
            ready: false,
            disarmed: false,
            text:
                'CALIBRATING · ' +
                parts
                    .map(item => `${item.id} ${Math.min(item.count, required)}/${required}`)
                    .join(' · ') +
                ' · keep nodes still'
        };
    }

    function updateControls() {
        const state = progress();
        const isReal = telemetry?.mode === 'REAL';

        zeroButton.disabled = !isReal;
        seismicButton.disabled =
            !isReal ||
            !state.ready ||
            alertState?.status === 'RED_ALERT';

        statusElement.textContent = state.text;
        statusElement.classList.toggle('armed', state.ready);
        statusElement.classList.toggle('disarmed', state.disarmed);
    }

    async function pollAlertState() {
        if (alertPollBusy) return;
        alertPollBusy = true;

        try {
            const response = await fetch('/api/alert/status', {cache: 'no-store'});
            if (!response.ok) return;

            alertState = await response.json();

            if (alertState.status === 'EARLY_WARNING') {
                showEarlyWarning(alertState);
            }
            else if (alertState.status === 'RED_ALERT') {
                hideEarlyWarning(false);
            }
            else {
                hideEarlyWarning(true);
            }

            updateControls();
        }
        catch (error) {
            console.warn('[TerraVeil] Stage 3 alert poll failed:', error);
        }
        finally {
            alertPollBusy = false;
        }
    }

    zeroButton.addEventListener('click', async () => {
        if (telemetry?.mode !== 'REAL') return;

        zeroButton.disabled = true;
        statusElement.textContent =
            'Starting fresh baseline… keep all physical nodes still.';

        try {
            const response = await fetch('/api/demo/baseline', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'SET ZERO failed');
            }

            alertState = data.alert_state || alertState;
            hideEarlyWarning(true);

            statusElement.textContent =
                `CALIBRATING · collecting ${data.baseline_samples_required || 5} fresh packets per node`;
        }
        catch (error) {
            console.error('[TerraVeil] SET ZERO failed:', error);
            statusElement.textContent = `SET ZERO FAILED · ${error.message}`;
        }
        finally {
            updateControls();
        }
    });

    seismicButton.addEventListener('click', async () => {
        seismicButton.disabled = true;

        try {
            const response = await fetch('/api/demo/seismic', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Geophone precursor simulation failed');
            }

            alertState = data.alert_state;
            showEarlyWarning(alertState);
        }
        catch (error) {
            console.error('[TerraVeil] Geophone simulation failed:', error);
            window.alert(error.message);
        }
        finally {
            updateControls();
        }
    });

    async function acknowledgeEarlyWarning() {
        try {
            const response = await fetch('/api/alert/reset', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Early warning acknowledgement failed');
            }

            alertState = data.alert_state;
            hideEarlyWarning(true);
            updateControls();
        }
        catch (error) {
            console.error('[TerraVeil] Early warning ACK failed:', error);
        }
    }

    window.addEventListener('terraveil:telemetry', event => {
        telemetry = event.detail;
        updateControls();
    });

    ensureEarlyOverlay();
    pollAlertState();
    setInterval(pollAlertState, 1000);
    updateControls();
})();
