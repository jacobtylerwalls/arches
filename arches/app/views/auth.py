"""
ARCHES - a program developed to inventory and manage immovable cultural heritage.
Copyright (C) 2013 J. Paul Getty Trust and World Monuments Fund

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

import base64
import io
import qrcode
import pyotp
import time
import requests
from datetime import datetime, timedelta
from django.http import HttpResponse
from django.http.response import HttpResponseForbidden
from django.template.loader import render_to_string
from django.views.generic import View
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.utils.html import strip_tags
from django.utils.translation import ugettext as _
from django.utils.http import urlencode
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
import django.contrib.auth.password_validation as validation
from arches import __version__
from arches.app.utils.response import JSONResponse, Http401Response
from arches.app.utils.forms import ArchesUserCreationForm, ArchesPasswordResetForm, ArchesSetPasswordForm
from arches.app.models import models
from arches.app.models.system_settings import settings
from arches.app.utils.arches_crypto import AESCipher
from arches.app.utils.betterJSONSerializer import JSONSerializer, JSONDeserializer
from arches.app.utils.permission_backend import user_is_resource_reviewer
import logging

logger = logging.getLogger(__name__)


class LoginView(View):
    def get(self, request):
        next = request.GET.get("next", reverse("home"))

        if request.GET.get("logout", None) is not None:
            logout(request)
            # need to redirect to 'auth' so that the user is set to anonymous via the middleware
            return redirect("auth")
        else:
            return render(
                request, 
                "login.htm", 
                {
                    'ENABLE_TWO_FACTOR_AUTHENTICATION': settings.ENABLE_TWO_FACTOR_AUTHENTICATION,
                    'FORCE_TWO_FACTOR_AUTHENTICATION': settings.FORCE_TWO_FACTOR_AUTHENTICATION,
                    "auth_failed": False, 
                    "next": next
                }
            )

    def post(self, request):
        # POST request is taken to mean user is logging in
        username = request.POST.get("username", None)
        password = request.POST.get("password", None)
        user = authenticate(username=username, password=password)
        next = request.POST.get("next", reverse("home"))
        two_factor_authentication_string = request.POST.get('two-factor-authentication', None)

        if user is not None and user.is_active:
            user_profile = models.UserProfile.objects.get(user=user)

            if (
                settings.FORCE_TWO_FACTOR_AUTHENTICATION
                or (
                    settings.ENABLE_TWO_FACTOR_AUTHENTICATION and user_profile.mfa_hash
                )
            ):
                user_profile = models.UserProfile.objects.get(user=user)
                totp = pyotp.TOTP(user_profile.mfa_hash)

                if totp.verify(two_factor_authentication_string):
                    login(request, user)
                    user.password = ""

                    return redirect(next)
            else:
                login(request, user)
                user.password = ""

                return redirect(next)

        return render(
            request, 
            "login.htm", 
            {
                'ENABLE_TWO_FACTOR_AUTHENTICATION': settings.ENABLE_TWO_FACTOR_AUTHENTICATION,
                'FORCE_TWO_FACTOR_AUTHENTICATION': settings.FORCE_TWO_FACTOR_AUTHENTICATION,
                "auth_failed": True, 
                "next": next,
            }, 
            status=401
        )



@method_decorator(never_cache, name="dispatch")
class BazView(View):
    def get(self, request):
        link = request.GET.get("link", None)
        # AES = AESCipher(settings.SECRET_KEY)

        # foo = JSONDeserializer().deserialize(AES.decrypt(link))

        # if datetime.fromtimestamp(foo["ts"]) + timedelta(minutes=15) >= datetime.fromtimestamp(int(time.time())):
        #     user_profile = models.UserProfile.objects.get(user=request.user)

        #     context = {
        #         'ENABLE_TWO_FACTOR_AUTHENTICATION': settings.ENABLE_TWO_FACTOR_AUTHENTICATION,
        #         'FORCE_TWO_FACTOR_AUTHENTICATION': settings.FORCE_TWO_FACTOR_AUTHENTICATION,
        #         'user_has_enabled_two_factor_authentication': bool(user_profile.mfa_hash),
        #     }

        # else:
        #     raise("ERROR")

        return render(request, 'baz.htm', {})
        # # return render(request, 'foo.htm', {'foo': base64_encoded_result_str })

    def post(self, request):
        # look up email, if valid send instructions
        pass

        return render(request, "baz.htm", {"auth_failed": True,})

@method_decorator(never_cache, name="dispatch")
class BarView(View):
    # def get(self, request):
    #     form = ArchesUserCreationForm(enable_captcha=settings.ENABLE_CAPTCHA)
    #     postdata = {"first_name": "", "last_name": "", "email": ""}
    #     showform = True
    #     confirmation_message = ""

    #     return render(
    #         request,
    #         "signup.htm",
    #         {
    #             "enable_captcha": settings.ENABLE_CAPTCHA,
    #             "form": form,
    #             "postdata": postdata,
    #             "showform": showform,
    #             "confirmation_message": confirmation_message,
    #             "validation_help": validation.password_validators_help_texts(),
    #         },
    #     )

    def post(self, request):
        AES = AESCipher(settings.SECRET_KEY)

        # import pdb; pdb.set_trace()
        # request.user["ts"] = int(time.time())  # add timestamp so link can expire

        foo = JSONSerializer().serialize({ 'ts': int(time.time()), 'user': request.user })


        bar = AES.encrypt(foo)
        baz = urlencode({"link": bar})


        admin_email = settings.ADMINS[0][1] if settings.ADMINS else ""
        email_context = {
            "button_text": _("Update Two-Factor Authentication Settings"),
            "link": request.build_absolute_uri(reverse("foo") + "?" + baz),
            "greeting": _(
                "Click on link below to update your two-factor authentication settings."
            ),
            "closing": _(
                "This link expires in 15 minutes. If you did not request this change, \
                contact your Administrator immediately."
            ),
        }

        html_content = render_to_string("email/general_notification.htm", email_context)  # ...
        text_content = strip_tags(html_content)  # this strips the html, so people will have the text as well.

        # create the email, and attach the HTML version as well.
        msg = EmailMultiAlternatives(_("Arches Two-Factor Authentication"), text_content, admin_email, [request.user.email])
        msg.attach_alternative(html_content, "text/html")

        msg.send()

        return JSONResponse(status=200)


class FooView(View):
    def get(self, request):
        link = request.GET.get("link", None)
        AES = AESCipher(settings.SECRET_KEY)

        foo = JSONDeserializer().deserialize(AES.decrypt(link))

        if True:
        # if datetime.fromtimestamp(foo["ts"]) + timedelta(minutes=15) >= datetime.fromtimestamp(int(time.time())):
            user_profile = models.UserProfile.objects.get(user=request.user)

            context = {
                'ENABLE_TWO_FACTOR_AUTHENTICATION': settings.ENABLE_TWO_FACTOR_AUTHENTICATION,
                'FORCE_TWO_FACTOR_AUTHENTICATION': settings.FORCE_TWO_FACTOR_AUTHENTICATION,
                'user_has_enabled_two_factor_authentication': bool(user_profile.mfa_hash),
            }

        else:
            raise("ERROR")

        return render(request, 'foo.htm', context)
        # return render(request, 'foo.htm', {'foo': base64_encoded_result_str })
        
    def post(self, request):
        user_profile = models.UserProfile.objects.get(user=request.user)

        generate_qr_code = request.POST.get('generate-qr-code-button')
        generate_manual_key = request.POST.get('generate-manual-key-button')
        delete_mfa_hash = request.POST.get('delete-mfa-hash-button')

        new_mfa_hash_qr_code = None
        new_mfa_hash_manual_entry_data = None
        
        if generate_qr_code or generate_manual_key:
            user_profile.mfa_hash = pyotp.random_base32()

            if generate_qr_code:
                uri = pyotp.totp.TOTP(user_profile.mfa_hash).provisioning_uri(request.user.email, issuer_name=settings.APP_TITLE)

                img = qrcode.make(uri)

                foobar = io.BytesIO()

                img.save(foobar)

                base64_encoded_result_bytes = base64.b64encode(foobar.getvalue())

                new_mfa_hash_qr_code = base64_encoded_result_bytes.decode('ascii')

                foobar.close()
            elif generate_manual_key:
                new_mfa_hash_manual_entry_data = {
                    'new_mfa_hash': user_profile.mfa_hash,
                    'name': request.user.email,
                    'issuer_name': settings.APP_TITLE
                }

        elif delete_mfa_hash and not settings.FORCE_TWO_FACTOR_AUTHENTICATION:
            user_profile.mfa_hash = None

        user_profile.save()

        context = {
            'ENABLE_TWO_FACTOR_AUTHENTICATION': settings.ENABLE_TWO_FACTOR_AUTHENTICATION,
            'FORCE_TWO_FACTOR_AUTHENTICATION': settings.FORCE_TWO_FACTOR_AUTHENTICATION,
            'user_has_enabled_two_factor_authentication': bool(user_profile.mfa_hash),
            'new_mfa_hash_qr_code': new_mfa_hash_qr_code,
            'new_mfa_hash_manual_entry_data': new_mfa_hash_manual_entry_data,
        }

        return render(request, "foo.htm", context)


@method_decorator(never_cache, name="dispatch")
class SignupView(View):
    def get(self, request):
        form = ArchesUserCreationForm(enable_captcha=settings.ENABLE_CAPTCHA)
        postdata = {"first_name": "", "last_name": "", "email": ""}
        showform = True
        confirmation_message = ""

        return render(
            request,
            "signup.htm",
            {
                "enable_captcha": settings.ENABLE_CAPTCHA,
                "form": form,
                "postdata": postdata,
                "showform": showform,
                "confirmation_message": confirmation_message,
                "validation_help": validation.password_validators_help_texts(),
            },
        )

    def post(self, request):
        showform = True
        confirmation_message = ""
        postdata = request.POST.copy()
        postdata["ts"] = int(time.time())
        form = ArchesUserCreationForm(postdata, enable_captcha=settings.ENABLE_CAPTCHA)

        if form.is_valid():
            AES = AESCipher(settings.SECRET_KEY)
            userinfo = JSONSerializer().serialize(form.cleaned_data)
            encrypted_userinfo = AES.encrypt(userinfo)
            url_encrypted_userinfo = urlencode({"link": encrypted_userinfo})


            admin_email = settings.ADMINS[0][1] if settings.ADMINS else ""
            email_context = {
                "button_text": _("Signup for Arches"),
                "link": request.build_absolute_uri(reverse("confirm_signup") + "?" + url_encrypted_userinfo),
                "greeting": _(
                    "Thanks for your interest in Arches. Click on link below \
                    to confirm your email address! Use your email address to login."
                ),
                "closing": _(
                    "This link expires in 24 hours.  If you can't get to it before then, \
                    don't worry, you can always try again with the same email address."
                ),
            }

            html_content = render_to_string("email/general_notification.htm", email_context)  # ...
            text_content = strip_tags(html_content)  # this strips the html, so people will have the text as well.



            # create the email, and attach the HTML version as well.
            msg = EmailMultiAlternatives(_("Welcome to Arches!"), text_content, admin_email, [form.cleaned_data["email"]])
            msg.attach_alternative(html_content, "text/html")


            # wrap in try/except with better message

            msg.send()



            confirmation_message = _(
                "An email has been sent to <br><strong>%s</strong><br> with a link to activate your account" % form.cleaned_data["email"]
            )
            showform = False

        return render(
            request,
            "signup.htm",
            {
                "enable_captcha": settings.ENABLE_CAPTCHA,
                "form": form,
                "postdata": postdata,
                "showform": showform,
                "confirmation_message": confirmation_message,
                "validation_help": validation.password_validators_help_texts(),
            },
        )
        

@method_decorator(never_cache, name="dispatch")
class ConfirmSignupView(View):
    def get(self, request):
        link = request.GET.get("link", None)
        AES = AESCipher(settings.SECRET_KEY)
        userinfo = JSONDeserializer().deserialize(AES.decrypt(link))
        form = ArchesUserCreationForm(userinfo)
        if datetime.fromtimestamp(userinfo["ts"]) + timedelta(days=1) >= datetime.fromtimestamp(int(time.time())):
            if form.is_valid():
                user = form.save()
                crowdsource_editor_group = Group.objects.get(name=settings.USER_SIGNUP_GROUP)
                user.groups.add(crowdsource_editor_group)
                return redirect("auth")
            else:
                try:
                    for error in form.errors.as_data()["username"]:
                        if error.code == "unique":
                            return redirect("auth")
                except:
                    pass
        else:
            form.errors["ts"] = [_("The signup link has expired, please try signing up again.  Thanks!")]

        return render(
            request,
            "signup.htm",
            {"form": form, "showform": True, "postdata": userinfo, "validation_help": validation.password_validators_help_texts()},
        )


@method_decorator(login_required, name="dispatch")
class ChangePasswordView(View):
    def get(self, request):
        messages = {"invalid_password": None, "password_validations": None, "success": None, "other": None, "mismatched": None}
        return JSONResponse(messages)

    def post(self, request):
        messages = {"invalid_password": None, "password_validations": None, "success": None, "other": None, "mismatched": None}
        try:
            user = request.user
            old_password = request.POST.get("old_password")
            new_password = request.POST.get("new_password")
            new_password2 = request.POST.get("new_password2")
            if user.check_password(old_password) == False:
                messages["invalid_password"] = _("Invalid password")
            if new_password != new_password2:
                messages["mismatched"] = _("New password and confirmation must match")
            try:
                validation.validate_password(new_password, user)
            except ValidationError as val_err:
                messages["password_validations"] = val_err.messages

            if messages["invalid_password"] is None and messages["password_validations"] is None and messages["mismatched"] is None:
                user.set_password(new_password)
                user.save()
                authenticated_user = authenticate(username=user.username, password=new_password)
                login(request, authenticated_user)
                messages["success"] = _("Password successfully updated")

        except Exception as err:
            messages["other"] = err

        return JSONResponse(messages)


class PasswordResetView(auth_views.PasswordResetView):
    form_class = ArchesPasswordResetForm


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    form_class = ArchesSetPasswordForm


@method_decorator(csrf_exempt, name="dispatch")
class UserProfileView(View):
    def post(self, request):
        username = request.POST.get("username", None)
        password = request.POST.get("password", None)
        user = authenticate(username=username, password=password)
        if user:
            if hasattr(user, "userprofile") is not True:
                models.UserProfile.objects.create(user=user)
            userDict = JSONSerializer().serializeToPython(user)
            userDict["password"] = None
            userDict["is_reviewer"] = user_is_resource_reviewer(user)
            userDict["viewable_nodegroups"] = user.userprofile.viewable_nodegroups
            userDict["editable_nodegroups"] = user.userprofile.editable_nodegroups
            userDict["deletable_nodegroups"] = user.userprofile.deletable_nodegroups
            response = JSONResponse(userDict)
        else:
            response = Http401Response()

        return response


@method_decorator(csrf_exempt, name="dispatch")
class GetClientIdView(View):
    def post(self, request):
        if settings.MOBILE_OAUTH_CLIENT_ID == "":
            message = _("Make sure to set your MOBILE_OAUTH_CLIENT_ID in settings.py")
            response = HttpResponse(message, status=500)
            logger.warning(message)
        else:
            username = request.POST.get("username", None)
            password = request.POST.get("password", None)
            user = authenticate(username=username, password=password)
            if user:
                response = JSONResponse({"clientid": settings.MOBILE_OAUTH_CLIENT_ID})
            else:
                response = Http401Response()
        return response


@method_decorator(csrf_exempt, name="dispatch")
class ServerSettingView(View):
    def post(self, request):
        if settings.MOBILE_OAUTH_CLIENT_ID == "":
            message = _("Make sure to set your MOBILE_OAUTH_CLIENT_ID in settings.py")
            logger.warning(message)

        username = request.POST.get("username", None)
        password = request.POST.get("password", None)
        user = authenticate(username=username, password=password)
        if user:
            server_settings = {"version": __version__, "clientid": settings.MOBILE_OAUTH_CLIENT_ID}
            response = JSONResponse(server_settings)
        else:
            response = Http401Response()

        return response


@method_decorator(csrf_exempt, name="dispatch")
class Token(View):
    def get(self, request):
        if settings.DEBUG:
            data = {
                "username": request.GET.get("username", None),
                "password": request.GET.get("password", None),
                "client_id": settings.MOBILE_OAUTH_CLIENT_ID,
                "grant_type": "password",
            }
            url = request.get_raw_uri().replace(request.path, "").split("?")[0] + reverse("oauth2:token")
            r = requests.post(url, data=data)
            return JSONResponse(r.json(), indent=4)
        return HttpResponseForbidden()
