'use strict';
const assert=require('node:assert/strict');const {indent,newline,highlight}=require('../web/editor.js');
assert.deepEqual(indent('x',0,0),{text:'    x',start:4,end:4});
assert.equal(indent('  x',2,2).text,'    x');
assert.deepEqual(indent('    x',4,4,true),{text:'x',start:0,end:0});
assert.deepEqual(indent('  x',1,1,true),{text:'x',start:0,end:0});
assert.equal(indent('a\nb\n',0,4).text,'    a\n    b\n');
assert.equal(indent('a\nb\n',0,2).text,'    a\nb\n');
assert.equal(indent('    a\n  b',0,9,true).text,'a\nb');
assert.equal(indent('\ta\n\tb',0,5,true).text,'a\nb');
assert.equal(newline('if ready:',9,9).text,'if ready:\n    ');
assert.equal(newline('    return value',16,16).text,'    return value\n    ');
assert.equal(newline('abc',1,2).text,'a\nc');
const sample='"""docstring\nстрока"""\ndef run(mart, params=None):\n    # comment\n    print("<script>", 0.42)\n';
const colored=highlight(sample);assert(colored.includes('py-string'));assert(colored.includes('py-keyword'));assert(colored.includes('py-comment'));assert(colored.includes('py-number'));assert(!colored.includes('<script>'));
const plain=colored.replace(/<span class="py-[^"]+">/g,'').replace(/<\/span>/g,'').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
assert.equal(plain,sample); // Highlighting never changes the actual source.
assert.equal(highlight('"""unfinished\nstring'),'<span class="py-string">&quot;&quot;&quot;unfinished\nstring</span>');
console.log('Editor tests: PASS (indentation, selections, autoindent, strings, escaping)');
