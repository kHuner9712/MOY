const fs = require('fs');
const path = require('path');

function walk(dir) {
    const files = fs.readdirSync(dir);
    for (const f of files) {
        const p = path.join(dir, f);
        if (fs.statSync(p).isDirectory()) {
            walk(p);
        } else if (p.endsWith('.ts') || p.endsWith('.mts')) {
            let content = fs.readFileSync(p, 'utf8');
            let updated = content.replace(/\.m?ts(['"])/g, '$1');
            if (content !== updated) {
                fs.writeFileSync(p, updated, 'utf8');
                console.log('Fixed', p);
            }
        }
    }
}

walk('./tests');
