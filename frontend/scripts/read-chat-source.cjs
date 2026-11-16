'use strict';
// The chat workspace is GrowthGradualChat.tsx plus the modules split out of it in components/chat/.
// Source-reading tests check the whole of it, so moving code between these files does not break them.
const fs = require('node:fs');
const path = require('node:path');

function readChatSource() {
  const components = path.join(__dirname, '..', 'src', 'components');
  const dir = path.join(components, 'chat');
  const parts = [fs.readFileSync(path.join(components, 'GrowthGradualChat.tsx'), 'utf8')];
  if (fs.existsSync(dir)) {
    for (const name of fs.readdirSync(dir).sort()) {
      if (/\.(tsx?|css)$/.test(name)) parts.push(fs.readFileSync(path.join(dir, name), 'utf8'));
    }
  }
  return parts.join('\n');
}

module.exports = { readChatSource };
