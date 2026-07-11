import '@testing-library/jest-dom/vitest';
const values = new Map<string,string>();
const storage = {getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key),clear:()=>values.clear(),key:(index:number)=>[...values.keys()][index]??null,get length(){return values.size}} as Storage;
Object.defineProperty(globalThis,'localStorage',{value:storage,configurable:true});
