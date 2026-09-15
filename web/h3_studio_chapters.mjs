// Presentation only: all segment times and source windows remain untouched.
export function studioChapterGroups(chapters, rows, segments) {
    const starts = chapters.map(chapter => ({...chapter,
        index:rows.findIndex(row => String(row.id) === chapter.start_scene_id),
    })).filter(chapter => chapter.index >= 0).sort((a, b) => a.index - b.index);
    return starts.map((chapter, offset) => {
        const nextIndex = starts[offset + 1]?.index ?? rows.length;
        const owns = segment => segment?.kind === "scene"
            && segment.sceneIndex >= chapter.index && segment.sceneIndex < nextIndex;
        const members = segments.filter((segment, index) => owns(segment)
            || (segment.kind === "gap" && !segment.trailing
                && owns(segments[index - 1]) && owns(segments[index + 1])));
        return {...chapter, segments:members,
            sceneCount:members.filter(segment => segment.kind === "scene").length,
            durationSeconds:members.reduce((sum, segment) => sum + segment.durationSeconds, 0),
        };
    });
}

export function studioChapterViewKey(run, branch) {
    return JSON.stringify([String(run ?? ""), String(branch ?? "main")]);
}

export function studioChapterView(saved, key, chapters) {
    const value = saved?.[key];
    const valid = new Set(chapters.map(chapter => chapter.id));
    return {
        collapsed:(Array.isArray(value?.collapsed) ? value.collapsed : [])
            .filter((id, index, ids) => valid.has(id) && ids.indexOf(id) === index),
        focused:valid.has(value?.focused) ? value.focused : "",
    };
}

export function studioChapterEntries(segments, groups, collapsed) {
    const owner = new Map();
    for (const group of groups) {
        if (!collapsed.includes(group.id)) continue;
        for (const segment of group.segments) owner.set(segment.key, group);
    }
    const entries = [];
    for (const segment of segments) {
        const chapter = owner.get(segment.key);
        const previous = entries.at(-1);
        if (chapter && previous?.chapter?.id === chapter.id) {
            previous.segments.push(segment);
            previous.endSeconds = segment.endSeconds;
            previous.durationSeconds += segment.durationSeconds;
        } else entries.push({
            key:chapter ? `chapter:${chapter.id}:${segment.key}` : segment.key,
            chapter, segments:[segment], startSeconds:segment.startSeconds,
            endSeconds:segment.endSeconds, durationSeconds:segment.durationSeconds,
        });
    }
    return entries;
}

export function studioChapterLayout(layout, entries) {
    let cursor = 0;
    const positions = entries.map(entry => {
        // A folded chapter occupies at most one compact card. It never grows
        // an already smaller chapter, and zoom does not unfold it implicitly.
        const width = Math.min(entry.durationSeconds * layout.pixelsPerSecond,
            entry.chapter ? 160 : Infinity);
        const result = {...entry, left:cursor, width};
        cursor += width;
        return result;
    });
    const viewportWidth = layout.pixelsPerSecond * Math.max(1 / 24,
        layout.packedSceneSeconds || layout.sceneEndSeconds || layout.totalSeconds) / layout.zoom;
    return {...layout, entries:positions, contentWidth:Math.max(viewportWidth, cursor)};
}

export function studioChapterPixel(entries, seconds) {
    if (!entries.length) return 0;
    const entry = entries.find(item => seconds < item.endSeconds) ?? entries.at(-1);
    const ratio = Math.max(0, Math.min(1,
        (seconds - entry.startSeconds) / Math.max(Number.EPSILON, entry.durationSeconds)));
    return entry.left + entry.width * ratio;
}

export function studioChapterSecond(entries, pixel) {
    if (!entries.length) return 0;
    const entry = entries.find(item => pixel < item.left + item.width) ?? entries.at(-1);
    const ratio = Math.max(0, Math.min(1,
        (pixel - entry.left) / Math.max(Number.EPSILON, entry.width)));
    return entry.startSeconds + entry.durationSeconds * ratio;
}

export function studioChapterPlayback(model, group) {
    if (!group?.segments.length) return {...model, startSeconds:0,
        durationSeconds:model.totalSeconds, chapter:null};
    return {...model, chapter:group, segments:group.segments,
        startSeconds:group.segments[0].startSeconds,
        totalSeconds:group.segments.at(-1).endSeconds,
        durationSeconds:group.durationSeconds};
}

// Usually chapters are contiguous. If scenes have been moved across chapter
// boundaries, the local player stitches only that chapter's retained windows.
export function studioChapterLocalSecond(model, seconds) {
    if (!model.chapter) return Math.max(0, Math.min(model.totalSeconds, seconds));
    let elapsed = 0;
    for (const segment of model.segments) {
        if (seconds <= segment.endSeconds) return elapsed + Math.max(0,
            Math.min(segment.durationSeconds, seconds - segment.startSeconds));
        elapsed += segment.durationSeconds;
    }
    return elapsed;
}

export function studioChapterGlobalSecond(model, localSeconds) {
    if (!model.chapter) return Math.max(0, Math.min(model.totalSeconds, localSeconds));
    let remaining = Math.max(0, Math.min(model.durationSeconds, localSeconds));
    for (const segment of model.segments) {
        if (remaining < segment.durationSeconds) return segment.startSeconds + remaining;
        remaining -= segment.durationSeconds;
    }
    return model.totalSeconds;
}
