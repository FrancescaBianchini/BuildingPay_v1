# -*- coding: utf-8 -*-
import logging
from odoo import http, _
from odoo.http import request
from odoo.addons.auth_signup.controllers.main import AuthSignupHome

_logger = logging.getLogger(__name__)


class BuildingPaySignup(AuthSignupHome):
    """
    Estende il controller di registrazione standard di Odoo per:
    1. Mostrare campi aggiuntivi (nome, cognome, indirizzo, CF/P.IVA)
    2. Catturare il codice referrer dall'URL
    3. Dopo la registrazione, configurare il partner come Amministratore
       e collegare il referrer
    4. Inviare l'email di benvenuto
    """

    @http.route('/web/signup', type='http', auth='public', website=True, sitemap=False)
    def web_auth_signup(self, *args, **kw):
        """
        Override della pagina di registrazione per intercettare il referrer.
        Il link di registrazione ha la forma:
            /web/signup?referrer=CODICE123
        """
        # Salva il referrer_code in sessione se presente nell'URL
        referrer_code = kw.get('referrer') or request.params.get('referrer', '')
        if referrer_code:
            request.session['buildingpay_referrer_code'] = referrer_code

        # Controlla che il sito web corrente abbia una configurazione BuildingPay
        config = request.env['buildingpay.config'].sudo().get_config_for_website()
        if not config:
            # Nessuna configurazione BuildingPay: usa la registrazione standard
            return super().web_auth_signup(*args, **kw)

        # Aggiungi qcontext custom
        qcontext = self.get_auth_signup_qcontext()
        qcontext['referrer_code'] = referrer_code or request.session.get(
            'buildingpay_referrer_code', '')

        # Se il referrer_code è valido, mostriamo il nome del referrer
        if qcontext.get('referrer_code'):
            referrer = request.env['res.partner'].sudo().search([
                ('referrer_code', '=', qcontext['referrer_code']),
                ('is_amministratore', '=', True),
            ], limit=1)
            qcontext['referrer_partner'] = referrer

        if request.httprequest.method == 'GET':
            return request.render('buildingpay.signup_form', qcontext)

        # POST: elaborazione form registrazione
        return self._process_buildingpay_signup(qcontext, **kw)

    def _process_buildingpay_signup(self, qcontext, **kw):
        """Elabora il form di registrazione BuildingPay."""
        params = request.params

        # Campi richiesti
        required_fields = ['name', 'login', 'password', 'confirm_password',
                           'street', 'city', 'zip']
        errors = {}

        for field in required_fields:
            if not params.get(field):
                errors[field] = _('Campo obbligatorio')

        if params.get('password') != params.get('confirm_password'):
            errors['confirm_password'] = _('Le password non coincidono')

        if errors:
            qcontext.update({'error': errors})
            return request.render('buildingpay.signup_form', qcontext)

        try:
            # Crea l'utente tramite il meccanismo standard
            login = params.get('login', '').strip()
            password = params.get('password', '')
            name = params.get('name', '').strip()

            # Crea utente portale
            values = {
                'login': login,
                'name': name,
                'password': password,
            }

            # Chiama il signup standard di Odoo
            db, login_result, _ = request.env['res.users'].sudo().signup(values)

            # Recupera il partner appena creato
            new_user = request.env['res.users'].sudo().search([
                ('login', '=', login_result),
            ], limit=1)

            if new_user:
                partner = new_user.partner_id
                # Aggiorna il partner con tutti i dati inseriti
                partner_vals = {
                    'is_amministratore': True,
                    'street': params.get('street', ''),
                    'street2': params.get('street2', ''),
                    'city': params.get('city', ''),
                    'zip': params.get('zip', ''),
                    'fiscalcode': params.get('fiscalcode', ''),
                    'vat': params.get('vat', ''),
                    'phone': params.get('phone', ''),
                }

                # Paese / Stato
                country_id = params.get('country_id')
                if country_id:
                    partner_vals['country_id'] = int(country_id)
                state_id = params.get('state_id')
                if state_id:
                    partner_vals['state_id'] = int(state_id)

                # Referrer
                referrer_code = (params.get('referrer_code') or
                                 request.session.get('buildingpay_referrer_code', ''))
                if referrer_code:
                    referrer = request.env['res.partner'].sudo().search([
                        ('referrer_code', '=', referrer_code),
                        ('is_amministratore', '=', True),
                    ], limit=1)
                    if referrer:
                        partner_vals['referrer_id'] = referrer.id

                partner.sudo().write(partner_vals)

                # Pulisci referrer dalla sessione
                request.session.pop('buildingpay_referrer_code', None)

                # Invia email di benvenuto
                self._send_welcome_email(partner)

                _logger.info(
                    'BuildingPay: nuovo amministratore registrato: %s (%s)',
                    partner.name, partner.email
                )

        except Exception as e:
            _logger.error('BuildingPay signup error: %s', e)
            qcontext['error'] = {'general': str(e)}
            return request.render('buildingpay.signup_form', qcontext)

        # Redirect al login dopo la registrazione
        return request.redirect('/web/login?message=signup_success')

    def _send_welcome_email(self, partner):
        """Invia l'email di benvenuto al nuovo amministratore."""
        try:
            template = request.env.ref(
                'buildingpay.email_template_benvenuto_amministratore',
                raise_if_not_found=False,
            )
            if template:
                template.sudo().send_mail(partner.id, force_send=True)
        except Exception as e:
            _logger.error('BuildingPay: errore invio email benvenuto: %s', e)

    def _get_referral_url(self, partner):
        """Genera il link referral per un amministratore."""
        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if partner.referrer_code:
            return '%s/web/signup?referrer=%s' % (base_url, partner.referrer_code)
        return base_url
