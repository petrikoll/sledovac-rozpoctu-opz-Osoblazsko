import {expect,test,vi} from 'vitest';
import {api} from './api';

test('odpověď 204 po odstranění nečte jako JSON',async()=>{
  const json=vi.fn();
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:204,json}));
  await expect(api('/projects/test/lump-sum-spending/test',{method:'DELETE'})).resolves.toBeUndefined();
  expect(json).not.toHaveBeenCalled();
});
