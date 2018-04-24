#!/usr/bin/env python3
#
# Electron Cash - lightweight Bitcoin Cash client
# Copyright (C) 2012 thomasv@gitorious
# Copyright (C) 2018 calin.culianu@gmail.com
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import math
import re
from typing import Callable, Any
from .uikit_bindings import *
from . import utils
from .custom_objc import *

from electroncash.i18n import _
from electroncash import WalletStorage, Wallet
       

def Create_PWChangeVC(msg : str, hasPW : bool, isEnc : bool,
                      callback : Callable[[str,str,bool], None] # pass a callback that accepts oldPW, newPW, encrypt_wallet_bool
                      ) -> ObjCInstance:
    ret = PWChangeVC.pwChangeVCWithMessage_hasPW_isEncrypted_(ns_from_py(msg), hasPW, isEnc)
    utils.add_callback(ret, 'okcallback', callback)
    return ret

class PWChangeVC(UIViewController):
    okBut = objc_property()
    pw1 = objc_property()
    pw2 = objc_property()
    curPW = objc_property()
    hasPW = objc_property()
    isEnc = objc_property()
    msg = objc_property()
    colors = objc_property()
    encSW = objc_property()
    encTit = objc_property()
    
    @objc_classmethod
    def pwChangeVCWithMessage_hasPW_isEncrypted_(cls : ObjCInstance, msg : ObjCInstance, hasPW : bool, isEnc : bool) -> ObjCInstance:
        ret = PWChangeVC.new().autorelease()
        ret.hasPW = hasPW
        ret.isEnc = isEnc
        ret.msg = msg
        ret.modalPresentationStyle = UIModalPresentationOverFullScreen#UIModalPresentationOverCurrentContext
        ret.modalTransitionStyle = UIModalTransitionStyleCrossDissolve
        ret.disablesAutomaticKeyboardDismissal = False
        return ret
    
    @objc_method
    def dealloc(self) -> None:
        self.okBut = None
        self.hasPW = None
        self.isEnc = None
        self.curPW = None
        self.pw1 = None
        self.pw2 = None
        self.msg = None
        self.colors = None
        self.encSW = None
        self.encTit = None
        utils.remove_all_callbacks(self)
        send_super(__class__, self, 'dealloc')
    
    @objc_method
    def doChkOkBut(self) -> None:
        is_en = bool( (not self.hasPW or self.curPW.text) and self.pw1.text == self.pw2.text )
#        for a in [self.okBut, self.encSW, self.encTit]:
        for a in [self.okBut]:
            if a: utils.uiview_set_enabled(a, is_en)

  
    @objc_method
    def textFieldDidBeginEditing_(self, tf : ObjCInstance) -> None:
        if not utils.is_iphone(): return
        # try and center the password text fields on the screen.. this is an ugly HACK.
        # todo: fixme!
        sv = self.viewIfLoaded 
        if sv and isinstance(sv, UIScrollView):
            sb = UIScreen.mainScreen.bounds
            v = sv.subviews()[0]
            frame = v.frame
            frame.origin.y = 700 - frame.size.height
            o = UIApplication.sharedApplication.statusBarOrientation
            if o in [UIInterfaceOrientationLandscapeLeft,UIInterfaceOrientationLandscapeRight]:
                frame.origin.y -= 300
                #print("WAS LANDSCAPE")
            #print("frame=%f,%f,%f,%f"%(frame.origin.x,frame.origin.y,frame.size.width,frame.size.height))
            sv.scrollRectToVisible_animated_(frame, True)
        
    @objc_method
    def textFieldDidEndEditing_(self, tf : ObjCInstance) -> None:
        #print("textFieldDidEndEditing", tf.tag, tf.text)
        self.doChkOkBut()
        return True
    
    @objc_method
    def textFieldShouldReturn_(self, tf: ObjCInstance) -> bool:
        #print("textFieldShouldReturn", tf.tag)
        nextTf = self.view.viewWithTag_(tf.tag+100) if self.viewIfLoaded else None
        if nextTf and isinstance(nextTf, UITextField):
            nextTf.becomeFirstResponder()
        else:
            tf.resignFirstResponder()
        return True
    
    @objc_method
    def viewDidAppear_(self, animated : bool) -> None:
        pass

    
    @objc_method
    def loadView(self) -> None:
        is_encrypted = self.isEnc
        has_pw = self.hasPW
        msg = self.msg
        if msg is None:
            if not has_pw:
                msg = _('Your wallet is not protected.')
                msg += ' ' + _('Use this dialog to add a password to your wallet.')
            else:
                if not is_encrypted:
                    msg = _('Your bitcoins are password protected. However, your wallet file is not encrypted.')
                else:
                    msg = _('Your wallet is password protected and encrypted.')
                msg += ' ' + _('Use this dialog to change your password.')
        self.msg = msg
        objs = NSBundle.mainBundle.loadNibNamed_owner_options_("ChangePassword",None,None)
        v = objs[0]
        allviews = v.allSubviewsRecursively()
        for a in allviews:
            if isinstance(a, UILabel):
                # translate UI automatically since placeholder text has potential translations 
                a.text = _(a.text)
            elif isinstance(a, UITextField):
                a.delegate = self
                old = a.placeholder
                new = _(old)
                newcolon = _(old + ':').replace(':','')
                a.placeholder = new if new != old else newcolon
            elif isinstance(a, UIButton):
                a.setTitle_forState_(_(a.titleForState_(UIControlStateNormal)), UIControlStateNormal)
        msgLbl = v.viewWithTag_(20)
        msgLbl.text = msg
        utils.uiview_set_enabled(v.viewWithTag_(100), has_pw)
        utils.uiview_set_enabled(v.viewWithTag_(110), has_pw)
        sv = UIScrollView.alloc().initWithFrame_(CGRectMake(0,0,320,254)).autorelease()
        sv.contentSize = CGSizeMake(320,700)
        sv.backgroundColor = UIColor.colorWithRed_green_blue_alpha_(0.,0.,0.,0.3)
        sv.opaque = False
        sv.addSubview_(v)
        self.view = sv
        okBut = v.viewWithTag_(1000)
        self.okBut = okBut
        self.pw1 = v.viewWithTag_(210)
        self.pw2 = v.viewWithTag_(310)
        self.curPW = v.viewWithTag_(110)
        self.encSW = v.viewWithTag_(510)
        self.encSW.setOn_animated_(bool(is_encrypted or not has_pw), False)
        self.encTit = v.viewWithTag_(500)
        pwStrLbl = v.viewWithTag_(410)
        pwStrTitLbl = v.viewWithTag_(400)
        myGreen = UIColor.colorWithRed_green_blue_alpha_(0.0,0.75,0.0,1.0)
        self.colors =  {"Weak":UIColor.redColor, "Medium":UIColor.blueColor, "Strong":myGreen, "Very Strong": myGreen}

        cancelBut = v.viewWithTag_(2000)
        def onCancel(but_in : objc_id) -> None:
            but = ObjCInstance(but_in)
            self.dismissViewControllerAnimated_completion_(True,None)
        def onOk(but_in : objc_id) -> None:
            but = ObjCInstance(but_in)
            #print("but tag = ",but.tag)
            cb=utils.get_callback(self, 'okcallback')
            oldpw = self.curPW.text
            newpw = self.pw1.text
            enc = bool(self.encSW.isOn() and newpw)
            oldpw = oldpw if oldpw else None
            newpw = newpw if newpw else None
            def onCompletion() -> None:
                cb(oldpw, newpw, enc)
            self.dismissViewControllerAnimated_completion_(True,onCompletion)
        def onChg(oid : objc_id) -> None:
            tf = ObjCInstance(oid)
            #print("value changed ", tf.tag,str(":"),tf.text)
            if tf.tag == self.pw1.tag:
                if len(tf.text):
                    s = check_password_strength(tf.text)
                    pwStrLbl.text = _(s)
                    pwStrLbl.textColor = self.colors.get(s,UIColor.blackColor)
                    utils.uiview_set_enabled(pwStrTitLbl,True)
                else:
                    pwStrLbl.text = ""
                    utils.uiview_set_enabled(pwStrTitLbl,False)
            self.doChkOkBut()
        cancelBut.handleControlEvent_withBlock_(UIControlEventPrimaryActionTriggered,onCancel)
        okBut.handleControlEvent_withBlock_(UIControlEventPrimaryActionTriggered,onOk)
        self.pw1.handleControlEvent_withBlock_(UIControlEventEditingChanged,onChg)
        self.pw2.handleControlEvent_withBlock_(UIControlEventEditingChanged,onChg)
        if has_pw: self.curPW.handleControlEvent_withBlock_(UIControlEventEditingChanged,onChg)
        #make sure Ok button is disabled, pw strength is disabled, etc
        onChg(self.pw1.ptr)

def check_password_strength(password):
    '''
    Check the strength of the password entered by the user and return back the same
    :param password: password entered by user in New Password
    :return: password strength 'Weak' or 'Medium' or 'Strong' or 'Very Strong'
    '''
    password = password
    n = math.log(len(set(password)))
    num = re.search("[0-9]", password) is not None and re.match("^[0-9]*$", password) is None
    caps = password != password.upper() and password != password.lower()
    extra = re.match("^[a-zA-Z0-9]*$", password) is None
    score = len(password)*( n + caps + num + extra)/20
    password_strength = {0:"Weak",1:"Medium",2:"Strong",3:"Very Strong"}
    strength = min(3, int(score))
    return password_strength[strength] 


def prompt_password_local_runloop(vc : ObjCInstance, prompt : str = None, title : str = None) -> str:
    title =  _("Enter Password") if not title else title
    prompt = _("Enter your password to proceed") if not prompt else prompt
    tf = None
    retPW = None
    def tfConfigHandler(oid : objc_id) -> None:
        nonlocal tf
        tf = ObjCInstance(oid)
        tf.adjustsFontSizeToFitWidth = True
        tf.minimumFontSize = 9
        tf.placeholder = _("Enter Password")
        tf.backgroundColor = utils.uicolor_custom('password')
        tf.borderStyle = UITextBorderStyleBezel
        tf.clearButtonMode = UITextFieldViewModeWhileEditing
        tf.secureTextEntry = True
    def onOK() -> None:
        nonlocal retPW
        nonlocal tf
        retPW = tf.text if tf is not None else retPW
    utils.show_alert(
        vc = vc,
        title = title,
        message = prompt,
        actions = [ [ _("OK"), onOK ], [_("Cancel")] ],
        cancel = _("Cancel"),
        localRunLoop = True,
        uiTextFieldHandlers = [tfConfigHandler]
    )
    return retPW

def prompt_password_asynch(vc : ObjCInstance, onOk : Callable, prompt : str = None, title : str = None) -> ObjCInstance:
    title =  _("Enter Password") if not title else title
    prompt = _("Enter your password to proceed") if not prompt else prompt
    tf = None
    retPW = None
    def tfConfigHandler(oid : objc_id) -> None:
        nonlocal tf
        tf = ObjCInstance(oid)
        tf.adjustsFontSizeToFitWidth = True
        tf.minimumFontSize = 9
        tf.placeholder = _("Enter Password")
        tf.backgroundColor = utils.uicolor_custom('password')
        tf.borderStyle = UITextBorderStyleBezel
        tf.clearButtonMode = UITextFieldViewModeWhileEditing
        tf.secureTextEntry = True
    def MyOnOk() -> None:
        if callable(onOk): onOk(tf.text)

    alert = utils.show_alert(
        vc = vc,
        title = title,
        message = prompt,
        actions = [ [ _("OK"), MyOnOk ], [_("Cancel")] ],
        cancel = _("Cancel"),
        localRunLoop = False,
        uiTextFieldHandlers = [tfConfigHandler]
    )
    return alert