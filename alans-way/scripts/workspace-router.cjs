#!/usr/bin/env node
// workspace-router.cjs — failover launcher for the workspace browser MCP server.
//
// Routes the `workspace_browser` MCP server to the Mac browser host when the
// Mac is reachable, and transparently falls back to the VPS browser host when
// the Mac is offline/asleep. Exposes the same stdio MCP surface either way, so
// the Hermes side never knows which host answered.
//
// Why a launcher (not a proxy): both hosts serve identical tools through the
// same `browser-mcp.cjs` script. Deciding the backend at connection time and
// forwarding stdio transparently is the smallest correct mechanism. Hermes
// re-spawns a lazy MCP server when its child exits, so if the Mac dies
// mid-session the ssh child exits and the next connection re-runs this probe
// and routes to the VPS.
//
// Configuration (all optional unless noted):
//   --bot-id ID                required: this bot's tab-owner identity
//   --bot-name NAME            optional display name for the agent cursor
//   --mac-ssh USER@HOST        Mac ssh alias/host for the Mac path
//   --mac-node PATH            node binary on the Mac (default: the app's own runtime,
//                              or /opt/homebrew/bin/node for a source checkout —
//                              non-interactive ssh does not load Homebrew's PATH)
//   --mac-script PATH          browser-mcp.cjs path on the Mac
//                              (default: Intelio.app, then the Intelio source
//                              checkout, then the legacy Alan's Way bundles)
//   --vps-script PATH          browser-mcp.cjs path on this host
//                              (default: sibling copy, then the deployed copy)
//   --vps-connection PATH      VPS browser connection.json
//   --mac-state-file PATH      mac-watch state file
//                              (default: /var/lib/hermes-alans-way/mac-state.json)
//   --probe                    run the Mac probe once, print the decision,
//                              and exit — read-only, for install/verify checks
// Environment fallbacks: HERMES_WORKSPACE_BOT_ID, HERMES_BOT_NAME,
//   HERMES_WORKSPACE_MAC_SSH, HERMES_WORKSPACE_MAC_NODE,
//   HERMES_WORKSPACE_MAC_MCP, HERMES_WORKSPACE_VPS_MCP,
//   HERMES_WORKSPACE_CONNECTION, HERMES_MAC_STATE_FILE.
// With no Mac ssh configured the router always serves the local VPS host.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const botId = arg('--bot-id') || process.env.HERMES_WORKSPACE_BOT_ID || '';
const botName = arg('--bot-name') || process.env.HERMES_BOT_NAME || '';
const macSsh = arg('--mac-ssh') || process.env.HERMES_WORKSPACE_MAC_SSH || '';
const macNode = arg('--mac-node') || process.env.HERMES_WORKSPACE_MAC_NODE || '';
const INTELIO_MAC_SCRIPTS = [
  '~/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs',
  '/Applications/Intelio.app/Contents/Resources/app/scripts/browser-mcp.cjs',
  '~/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs',
];
const LEGACY_MAC_SCRIPTS = [
  '/Applications/alans-way-localapp.app/Contents/Resources/app/scripts/browser-mcp.cjs',
  '/Applications/Open Alan.app/Contents/Resources/app/scripts/browser-mcp.cjs',
  "/Applications/Hermes- Alan's way.app/Contents/Resources/app/scripts/browser-mcp.cjs",
  '/Applications/Hermes Workspace.app/Contents/Resources/app/scripts/browser-mcp.cjs',
];

function defaultMacScripts() {
  return [...INTELIO_MAC_SCRIPTS, ...LEGACY_MAC_SCRIPTS];
}

// Home-relative candidates expand on the Mac. Only a conservative relative
// path is interpolated; everything else stays single-quoted.
function macScriptProbeClause(script, alive) {
  if (script.startsWith('~/')) {
    const rel = script.slice(2);
    if (!/^[\w./-]+$/.test(rel) || rel.split('/').includes('..')) {
      throw new Error('workspace-router: refusing unsafe home-relative Mac script path');
    }
    const expanded = `"$HOME/${rel}"`;
    return `if [ -f ${expanded} ] && ${alive}; then printf %s ${expanded}; exit 0; fi`;
  }
  return `if [ -f ${shQuote(script)} ] && ${alive}; then printf %s ${shQuote(script)}; exit 0; fi`;
}

// The probe prints a path. Accept only a configured absolute path, or the
// home-expanded form of a ~/ candidate under /Users/<name> or /home/<name>.
function acceptProbedMacScript(output, scripts) {
  if (typeof output !== 'string' || output.length === 0 || output.length > 4096) return false;
  if (output.includes('\n') || output.includes('\0')) return false;
  if (scripts.includes(output)) return true;
  for (const script of scripts) {
    if (!script.startsWith('~/')) continue;
    const suffix = script.slice(1);
    if (!output.endsWith(suffix)) continue;
    const home = output.slice(0, output.length - suffix.length);
    if (/^\/(?:Users|home)\/[^/]+$/.test(home)) return true;
  }
  return false;
}

const configuredMacScript = arg('--mac-script') || process.env.HERMES_WORKSPACE_MAC_MCP;
const macScripts = configuredMacScript ? [configuredMacScript] : defaultMacScripts();
const siblingScript = path.join(__dirname, 'browser-mcp.cjs');
const vpsScript =
  arg('--vps-script') ||
  process.env.HERMES_WORKSPACE_VPS_MCP ||
  (fs.existsSync(siblingScript)
    ? siblingScript
    : '/opt/hermes-alans-way/browser/desktop/scripts/browser-mcp.cjs');
const vpsConnection =
  arg('--vps-connection') ||
  process.env.HERMES_WORKSPACE_CONNECTION ||
  path.join(os.homedir(), '.local', 'share', 'hermes-alans-way', 'browser', 'connection.json');
const macStateFile =
  arg('--mac-state-file') ||
  process.env.HERMES_MAC_STATE_FILE ||
  '/var/lib/hermes-alans-way/mac-state.json';

// Reuse one ssh connection between the probe and the backend spawn: the
// probe's handshake becomes the spawn's (~5ms vs a full handshake), and
// later respawns ride it for ControlPersist seconds. %C hashes the
// destination, so the socket name needs no host-derived parts.
const sshControlPath = path.join(process.env.TMPDIR || '/tmp', 'wsr-%C');
const sshControlArgs = [
  '-o',
  'ControlMaster=auto',
  '-o',
  'ControlPersist=120',
  '-o',
  `ControlPath=${sshControlPath}`,
];

// Quote a value for the remote command line ssh builds from argv.
const shQuote = (value) => `'${String(value).replace(/'/g, "'\\''")}'`;

// Non-interactive ssh never loads Homebrew's PATH, so a bare `node` is
// usually missing on the Mac. The app bundle already ships a Node runtime:
// its own Electron binary with ELECTRON_RUN_AS_NODE. PATH node is only the
// fallback for bundles without one; an explicit --mac-node always wins.
function macBackendCommand(script, node, id, name) {
  const tail = [shQuote(script), '--bot-id', shQuote(id)];
  if (name) tail.push('--bot-name', shQuote(name));
  const args = tail.join(' ');
  if (node) return `${shQuote(node)} ${args}`;
  const bundle = /^(.*\/([^/]+)\.app)\/Contents\/Resources\//.exec(script);
  if (!bundle) {
    // Source checkouts have no Electron binary. Non-interactive ssh does not
    // load Homebrew, so prefer the usual Apple Silicon node before PATH.
    return `if [ -x /opt/homebrew/bin/node ]; then exec /opt/homebrew/bin/node ${args}; else exec node ${args}; fi`;
  }
  const exe = shQuote(`${bundle[1]}/Contents/MacOS/${bundle[2]}`);
  return `if [ -x ${exe} ]; then ELECTRON_RUN_AS_NODE=1 exec ${exe} ${args}; else exec node ${args}; fi`;
}

// Probe finds the newest installed bundle AND proves the app is actually
// serving — a closed app still has the script on disk, so file-existence
// alone would route to a dead host. The API answers 401 without auth, which
// still proves liveness; a refused connection means the app is not running.
function probeMac(timeoutMs, connectTimeout = 6) {
  return new Promise((resolve) => {
    const alive =
      `{ conn="$HOME/Library/Application Support/Hermes Workspace/connection.json"; ` +
      `[ -f "$conn" ] && ` +
      `port=$(sed -n 's/.*"url"[^0-9]*[0-9.]*:\\([0-9]*\\).*/\\1/p' "$conn" | head -1) && ` +
      `curl -s -m 4 -o /dev/null "http://127.0.0.1:\${port:-9464}/status"; }`;
    const probe = macScripts
      .map(script => macScriptProbeClause(script, alive))
      .join('; ') + '; exit 1';
    const child = spawn(
      'ssh',
      [
        '-T',
        '-o',
        'BatchMode=yes',
        '-o',
        `ConnectTimeout=${connectTimeout}`,
        '-o',
        'StrictHostKeyChecking=yes',
        ...sshControlArgs,
        macSsh,
        probe,
      ],
      { stdio: ['ignore', 'pipe', 'ignore'] },
    );
    let output = '';
    child.stdout.on('data', chunk => { if (output.length < 4096) output += chunk; });
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      resolve(null);
    }, timeoutMs);
    child.on('error', () => {
      clearTimeout(timer);
      resolve(null);
    });
    child.on('exit', (code) => {
      clearTimeout(timer);
      resolve(code === 0 && acceptProbedMacScript(output, macScripts) ? output : null);
    });
  });
}

// Read the mac-watch state file. A missing or malformed file means
// "unknown": the router still decides on its own probe and reports mac: null.
function readMacState(file) {
  try {
    const doc = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!doc || (doc.state !== 'online' && doc.state !== 'offline')) return null;
    const text = (key) => (typeof doc[key] === 'string' ? doc[key].slice(0, 64) : null);
    return { state: doc.state, since: text('since'), lastSeenOnline: text('lastSeenOnline') };
  } catch {
    return null;
  }
}

// A state-file verdict only counts while mac-watch is alive to refresh it:
// the watcher rewrites the file every interval (~30s), so an mtime older
// than ~45s is a dead watcher's last word, not a current state.
function freshMacState(file, maxAgeMs = 45000) {
  try {
    if (Date.now() - fs.statSync(file).mtimeMs > maxAgeMs) return null;
  } catch {
    return null;
  }
  return readMacState(file);
}

// Human-readable line appended to tool results — the channel the agent sees.
// Only meaningful when this connection fell back to the VPS host.
function workspaceNotice(host, mac) {
  if (!mac || host !== 'vps') return null;
  if (mac.state === 'online') {
    return (
      `[workspace] Mac is back online as of ${mac.since || 'unknown'} — ` +
      'tasks waiting on Mac-local resources can resume.'
    );
  }
  return (
    `[workspace] Mac unreachable since ${mac.since || 'unknown'} — ` +
    'routed to VPS browser; Mac-local files unavailable.'
  );
}

// Additive decoration of one outbound JSON-RPC message: structured host state
// under result._meta.workspace plus, for tool results, the notice as an extra
// text content item. Anything not a result object passes through untouched.
function annotateResult(msg, host, mac, notice) {
  if (!msg || typeof msg !== 'object' || !msg.result || typeof msg.result !== 'object') return msg;
  msg.result._meta = { ...(msg.result._meta || {}), workspace: { host, mac } };
  if (notice && Array.isArray(msg.result.content)) {
    msg.result.content = [...msg.result.content, { type: 'text', text: notice }];
  }
  return msg;
}

// Per-connection line annotator for the backend's stdout. The state file is
// re-read per message so a mid-session flip is seen; the "back online" notice
// fires once per online transition while the offline one rides every tool
// result, since either may be the agent's only signal that host changed.
function makeAnnotator(host, macConfigured, stateFile) {
  let onlineAnnounced = false;
  return function annotateLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      return line;
    }
    if (!msg || typeof msg !== 'object' || !msg.result || typeof msg.result !== 'object') return line;
    const mac = macConfigured ? readMacState(stateFile) : null;
    let notice = null;
    if (mac && Array.isArray(msg.result.content)) {
      if (mac.state === 'online') {
        if (!onlineAnnounced) notice = workspaceNotice(host, mac);
        onlineAnnounced = true;
      } else {
        onlineAnnounced = false;
        notice = workspaceNotice(host, mac);
      }
    }
    return JSON.stringify(annotateResult(msg, host, mac, notice));
  };
}

async function main() {
  if (process.argv.includes('--probe')) {
    const found = macSsh ? await probeMac(12000) : null;
    process.stdout.write(
      found ? `mac: ${found}\n` : `vps${macSsh ? ' (mac unreachable)' : ' (no mac-ssh)'}\n`,
    );
    return;
  }

  if (!botId) {
    process.stderr.write(
      'workspace-router: --bot-id (or HERMES_WORKSPACE_BOT_ID) is required.\n',
    );
    process.exit(1);
  }
  // A fresh mac-watch "offline" verdict skips the probe entirely: the
  // watcher already paid the ssh timeout, so paying it again on every lazy
  // respawn just adds seconds while the Mac is down. A fresh "online" still
  // probes — the liveness check stays the authority — but on a shorter
  // connect timeout since the file may have just gone stale-positive. A
  // missing or stale file probes as before.
  const macSeen = macSsh ? freshMacState(macStateFile) : null;
  const macScript =
    macSsh && (!macSeen || macSeen.state === 'online')
      ? await probeMac(8000, macSeen ? 4 : 6)
      : null;

  let cmd;
  let args;
  if (macScript) {
    // Run browser-mcp.cjs on the Mac over the same ssh session the probe used.
    cmd = 'ssh';
    args = [
      '-T',
      '-o',
      'BatchMode=yes',
      '-o',
      'StrictHostKeyChecking=yes',
      ...sshControlArgs,
      macSsh,
      macBackendCommand(macScript, macNode, botId, botName),
    ];
    process.stderr.write('workspace-router: routing to Mac browser host\n');
  } else {
    // Mac unreachable or unconfigured — fall back to the local VPS browser host.
    cmd = process.execPath;
    args = [vpsScript, '--bot-id', botId];
    if (botName) args.push('--bot-name', botName);
    args.push('--connection', vpsConnection);
    const reason = macSeen && macSeen.state === 'offline' ? 'offline per mac-watch' : 'unreachable';
    process.stderr.write(
      macSsh
        ? `workspace-router: Mac ${reason} — routing to VPS browser host\n`
        : 'workspace-router: no Mac ssh configured — routing to VPS browser host\n',
    );
  }

  const child = spawn(cmd, args, { stdio: ['inherit', 'pipe', 'inherit'] });

  // Annotation is decoration — a failure here must never take down the
  // transport (that is how a one-line ReferenceError dropped the whole
  // browser surface). Degrade to passthrough instead.
  let annotate;
  try {
    annotate = makeAnnotator(macScript ? 'mac' : 'vps', Boolean(macSsh), macStateFile);
  } catch (e) {
    process.stderr.write(`workspace-router: annotator disabled: ${e.message}\n`);
    annotate = (line) => line;
  }

  let lastActivity = Date.now();
  readline
    .createInterface({ input: child.stdout, crlfDelay: Infinity })
    .on('line', (line) => {
      lastActivity = Date.now();
      let out;
      try {
        out = annotate(line);
      } catch {
        out = line;
      }
      process.stdout.write(out + '\n');
    });
  child.on('error', (e) => {
    process.stderr.write(`workspace-router: failed to spawn backend: ${e.message}\n`);
    process.exit(1);
  });
  child.on('exit', (code, sig) => {
    process.exit(code === null ? (sig ? 1 : 0) : code);
  });
  for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    process.on(s, () => {
      try {
        child.kill(s);
      } catch {}
    });
  }

  // Self-correct host drift. A connection that landed on the VPS during a
  // transient probe miss would otherwise pin the session to the wrong host
  // until someone killed the process by hand. Hermes lazy-respawns a dead
  // MCP server and the respawn re-probes, so once mac-watch reports the Mac
  // online again this process steps aside and routing re-converges on its
  // own — no agent shell surgery, no approvals. Two consecutive online
  // reads defend against flapping; the idle window keeps an in-flight tool
  // call alive.
  if (!macScript && macSsh) {
    let onlineStreak = 0;
    const timer = setInterval(() => {
      const mac = readMacState(macStateFile);
      onlineStreak = mac && mac.state === 'online' ? onlineStreak + 1 : 0;
      if (onlineStreak >= 2 && Date.now() - lastActivity > 60000) {
        process.stderr.write(
          'workspace-router: Mac is online — exiting so the next connection re-probes and routes to it\n',
        );
        try {
          child.kill('SIGTERM');
        } catch {}
        process.exit(0);
      }
    }, 30000);
    timer.unref();
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  readMacState,
  workspaceNotice,
  annotateResult,
  makeAnnotator,
  macBackendCommand,
  defaultMacScripts,
  macScriptProbeClause,
  acceptProbedMacScript,
};
