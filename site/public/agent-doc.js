import {$,$$,action,copy,L} from './common.js';
$$('pre').forEach(el=>el.after(action(L('Copy example','复制示例'),()=>void copy(el.textContent))));
