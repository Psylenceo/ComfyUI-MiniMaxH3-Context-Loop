// One pending refresh per editor, not one full rebuild per restored wire.
// Only a requested refresh waits for configuration; there is no idle poll.
export function coalescedRefresh(refresh, {
    isConfiguring = () => false,
    isAlive = () => true,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
} = {}) {
    let timer = null;
    let reload = false;

    function flush() {
        timer = null;
        if (!isAlive()) {
            reload = false;
            return;
        }
        if (isConfiguring()) {
            timer = setTimer(flush, 16);
            return;
        }
        const requestedReload = reload;
        reload = false;
        refresh(requestedReload);
    }

    function schedule(fullReload = false) {
        // A later connection-only notification must not discard a pending
        // reload of saved prompts, settings or editor layout.
        reload ||= fullReload;
        if (timer === null) timer = setTimer(flush, 0);
    }
    schedule.cancel = () => {
        if (timer !== null) clearTimer(timer);
        timer = null;
        reload = false;
    };
    return schedule;
}
