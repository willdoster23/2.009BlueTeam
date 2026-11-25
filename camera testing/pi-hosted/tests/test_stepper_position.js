'use strict';
const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),test=require('node:test');
const context={window:{},localStorage:{getItem:()=>null}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../app/storage.js'),'utf8'),context);
const storage=context.window.brickStorage;
test('saved stepper positions retain estimated X and measured pulley Y sources',()=>{
 const metadata=storage.positionMetadata({xInches:4,yInches:10,xEstimated:true,yZeroed:true,source:'outrigger-stepper-x-pulley-y'});
 assert.equal(metadata.position_source,'outrigger-stepper-x-pulley-y');
 assert.equal(metadata.x_estimated,true);assert.equal(metadata.y_inches,10);assert.equal(metadata.y_zeroed,true);
});
test('legacy measured positions retain their source and brick reservation keys',()=>{
 const metadata=storage.positionMetadata({xInches:4,yInches:10,yZeroed:true,source:'outrigger-encoders'});
 assert.equal(metadata.position_source,'outrigger-encoders');assert.equal(metadata.x_estimated,false);
 assert.equal(storage.brickKey({source:'measured',course:2,column:3}),'measured:2:3');
});
