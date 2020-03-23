""" ProxyProvider implementation for the proxy generation using local (DIRAC)
    CA credentials

    This class is a simple, limited CA, its main purpose is to generate a simple proxy for DIRAC users
    who do not have any certificate register on the fly.

    Required parameters in the DIRAC configuration for its implementation::

    <Provider Name> section::

      *  ProviderType = DIRACCA,
      *  CertFile = <CA sertificate path>,
      *  KeyFile = <CA key path>,
      *  Match = <Match DNs>,  # For ex.: 'Match = O, OU'
      *  Supplied = <Supplied DNs>,
      *  Optional = <Optional DNs>,
      *  DNOrder = <Preferred DNs order as list>,  # For ex.: 'DNOrder = O, OU, CN, emailAddress'
      *  <Some distinguished name type>: <Default value>,  # For ex.: 'OU = CA'

    Also, as an additional feature, this class can read properties from a simple openssl CA configuration file.
    To do this, just set the path to an existing configuration file as a CAConfigFile parameter. In this case,
    the distinguished names order in the created proxy will be the same as in the configuration file policy block.

    The Proxy provider supports the next distinguished names(https://www.cryptosys.net/pki/manpki/pki_distnames.html)::

      SN(surname)
      GN(givenName)
      C(countryName)
      CN(commonName)
      L(localityName)
      Email(emailAddress)
      O(organizationName)
      OU(organizationUnitName)
      SP,ST(stateOrProvinceName)
      SERIALNUMBER(serialNumber)

"""

import re
import time
import random
import datetime
import collections

from M2Crypto import m2, util, X509, ASN1, EVP, RSA

from DIRAC import gLogger, S_OK, S_ERROR
from DIRAC.Core.Security.X509Chain import X509Chain  # pylint: disable=import-error
from DIRAC.Resources.ProxyProvider.ProxyProvider import ProxyProvider

__RCSID__ = "$Id$"


class DIRACCAProxyProvider(ProxyProvider):

  def __init__(self, parameters=None):
    """ Constructor
    """
    super(DIRACCAProxyProvider, self).__init__(parameters)
    self.log = gLogger.getSubLogger(__name__)
    # Initialize
    self.maxDict = {}
    self.minDict = {}
    self.bits = 2048
    self.algoritm = 'sha256'
    self.match = []
    self.supplied = ['CN']
    self.optional = ['C', 'O', 'OU', 'emailAddress']
    self.dnList = ['C', 'O', 'OU', 'CN', 'emailAddress']
    # Distinguished names
    self.fields2nid = X509.X509_Name.nid.copy()
    self.fields2nid['DC'] = -1  # Add DN that is not liested in X509.X509_Name
    self.fields2nid['domainComponent'] = -1  # Add DN description that is not liested in X509.X509_Name
    self.fields2nid['organizationalUnitName'] = 18  # Add 'OU' description
    self.fields2nid['countryName'] = 14  # Add 'C' description
    self.fields2nid['SERIALNUMBER'] = 105  # Add 'SERIALNUMBER' distinguished name
    self.nid2field = {}  # nid: most short or specidied in CS distinguished name
    self.nid2fields = {}  # nid: list of distinguished names
    # Specify standart fields
    for field in self.fields2nid:
      if self.fields2nid[field] not in self.nid2fields:
        self.nid2fields[self.fields2nid[field]] = []
      self.nid2fields[self.fields2nid[field]].append(field)
    for nid in self.nid2fields:
      for field in self.nid2fields[nid]:
        if nid not in self.nid2field:
          self.nid2field[nid] = field
        self.nid2field[nid] = len(field) < len(self.nid2field[nid]) and field or self.nid2field[nid]
    self.dnInfoDictCA = {}

  def setParameters(self, parameters):
    """ Set new parameters

        :param dict parameters: provider parameters

        :return: S_OK()/S_ERROR()
    """
    # If CA configuration file exist
    self.parameters = parameters
    if parameters.get('CAConfigFile'):
      self.__parseCACFG()
    if 'Bits' in parameters:
      self.bits = int(parameters['Bits'])
    if 'Algoritm' in parameters:
      self.algoritm = parameters['Algoritm']
    if 'Match' in parameters:
      self.match = []
      if not isinstance(parameters['Match'], list):
        parameters['Match'] = parameters['Match'].replace(', ', ',').split(',')
      for field in parameters['Match']:
        self.match.append(self.fields2nid[field])
    if 'Supplied' in parameters:
      self.supplied = []
      if not isinstance(parameters['Supplied'], list):
        parameters['Supplied'] = parameters['Supplied'].replace(', ', ',').split(',')
      for field in parameters['Supplied']:
        self.supplied.append(self.fields2nid[field])
    if 'Optional' in parameters:
      self.optional = []
      if not isinstance(parameters['Optional'], list):
        parameters['Optional'] = parameters['Optional'].replace(', ', ',').split(',')
      for field in parameters['Optional']:
        self.optional.append(self.fields2nid[field])
    if 'DNOrder' in parameters:
      if isinstance(parameters['DNOrder'], list):
        self.dnList = parameters['DNOrder']
      else:
        self.dnList = parameters['DNOrder'].replace(', ', ',').split(',')

    # Set defaults for distridutes names
    for field, value in self.parameters.items():
      if field not in self.fields2nid:
        continue
      if self.fields2nid[field] not in self.optional + self.supplied + self.match:
        del self.parameters[field]
      elif not isinstance(self.parameters[field], list):
        self.parameters[field] = self.parameters[field].replace(', ', ',').split(',')

    self.defDict = {}
    for field, value in parameters.items():
      if field in self.fields2nid:
        self.defDict[field] = value
    self.defFieldByNid = dict([[self.fields2nid[field], field] for field in self.defDict])
    for nid in self.nid2field:
      if nid in self.defFieldByNid:
        self.nid2field[nid] = self.defFieldByNid[nid]
    self.match.sort()
    self.supplied.sort()

    # Read CA certificate
    chain = X509Chain()
    result = chain.loadChainFromFile(self.parameters['CertFile'])
    if result['OK']:
      result = chain.getCredentials()
      if result['OK']:
        result = self.__parseDN(result['Value']['subject'])
    if not result['OK']:
      return result
    self.dnInfoDictCA = result['Value']
    return S_OK()

  def checkStatus(self, userDN):
    """ Read ready to work status of proxy provider

        :param str userDN: user DN

        :return: S_OK(dict)/S_ERROR() -- dictionary contain fields:
                  - 'Status' with ready to work status[ready, needToAuth]
    """
    self.log.debug('Ckecking work status of', self.parameters['ProviderName'])
    result = self.__parseDN(userDN)
    if not result['OK']:
      return result
    dnInfoDict = result['Value']

    try:
      userNIDs = [self.fields2nid[f.split('=')[0]] for f in userDN.lstrip('/').split('/')]
    except (ValueError, KeyError) as e:
      return S_ERROR('Unknown DN field in used DN: %s' % e)
    nidOrder = [self.fields2nid[f] for f in self.dnList]
    for index, nid in enumerate(userNIDs):
      if nid not in nidOrder:
        return S_ERROR('"%s" field not found in order.' % self.nid2field[nid])
      if index > nidOrder.index(nid):
        return S_ERROR('Bad DNs order')
      for i in range(nidOrder.index(nid) - 1):
        try:
          if userNIDs.index(nidOrder[i]) > index:
            return S_ERROR('Bad DNs order')
        except (ValueError, KeyError):
          continue
      for i in range(nidOrder.index(nid) + 1, len(nidOrder)):
        try:
          if userNIDs.index(nidOrder[i]) < index:
            return S_ERROR('Bad DNs order')
        except (ValueError, KeyError):
          continue

    for nid in self.supplied:
      if nid not in [self.fields2nid[f] for f in dnInfoDict]:
        return S_ERROR('Current DN is invalid, "%s" field must be set.' % self.nid2field[nid])

    for field, values in dnInfoDict.items():
      nid = self.fields2nid[field]
      err = 'Current DN is invalid, "%s" field' % field
      if nid not in self.supplied + self.match + self.optional:
        return S_ERROR('%s is not found for current CA.' % err)
      if nid in self.match and not self.dnInfoDictCA[field] == values:
        return S_ERROR('%s must be /%s=%s.' % (err, field,
                                               ('/%s=' % field).joing(self.dnInfoDictCA[field])))
      if nid in self.maxDict:
        rangeMax = range(min(len(values), len(self.maxDict[nid])))
        if any([True if len(values[i]) > self.maxDict[nid][i] else False for i in rangeMax]):
          return S_ERROR('%s values must be less then %s.' % (err, ', '.join(self.maxDict[nid])))
      if nid in self.minDict:
        rangeMin = range(min(len(values), len(self.minDict[nid])))
        if any([True if len(values[i]) < self.minDict[nid][i] else False for i in rangeMin]):
          return S_ERROR('%s values must be more then %s.' % (err, ', '.join(self.minDict[nid])))

      result = self.__fillX509Name(field, values)
      if not result['OK']:
        return result

    return S_OK({'Status': 'ready'})

  def getProxy(self, userDN):
    """ Generate user proxy

        :param str userDN: user DN

        :return: S_OK(dict)/S_ERROR() -- dict contain 'proxy' field with is a proxy string
    """
    self.__X509Name = X509.X509_Name()
    result = self.checkStatus(userDN)
    if result['OK']:
      result = self.__createCertM2Crypto()
      if result['OK']:
        certStr, keyStr = result['Value']

        chain = X509Chain()
        result = chain.loadChainFromString(certStr)
        if result['OK']:
          result = chain.loadKeyFromString(keyStr)
          if result['OK']:
            result = chain.generateProxyToString(365 * 24 * 3600, rfc=True)

    if not result['OK']:
      return result
    return S_OK({'proxy': result['Value']})

  def generateDN(self, **kwargs):
    """ Get DN of the user certificate that will be created

        :param dict kwargs: user description dictionary with possible fields:
               - FullName or CN
               - Email or emailAddress

        :return: S_OK(str)/S_ERROR() -- contain DN
    """
    if kwargs.get('FullName'):
      kwargs['CN'] = [kwargs['FullName']]
    if kwargs.get('Email'):
      kwargs['emailAddress'] = [kwargs['Email']]

    self.__X509Name = X509.X509_Name()
    self.log.info('Creating distinguished names chain')

    for nid in self.supplied:
      if nid not in [self.fields2nid[f] for f in self.dnList]:
        return S_ERROR('DNs order list does not contain supplied DN "%s"' % self.nid2field[nid])

    for field in self.dnList:
      values = []
      nid = self.fields2nid[field]
      if nid in self.match:
        for field in self.nid2fields[nid]:
          if field in self.dnInfoDictCA:
            values = self.dnInfoDictCA[field]
        if not values:
          return S_ERROR('Not found "%s" match DN in CA' % field)
      for field in self.nid2fields[nid]:
        if kwargs.get(field):
          values = kwargs[field] if isinstance(kwargs[field], list) else [kwargs[field]]
      if not values:
        for field in self.nid2fields[nid]:
          if self.parameters.get(field):
            values = self.parameters[field]
      if not values and nid in self.supplied:
        return S_ERROR('No values set for "%s" DN' % field)

      result = self.__fillX509Name(field, values)
      if not result['OK']:
        return result

    # WARN: This logic not support list of distribtes name elements
    resDN = m2.x509_name_oneline(self.__X509Name.x509_name)  # pylint: disable=no-member

    result = self.checkStatus(resDN)
    if not result['OK']:
      return result
    return S_OK(resDN)

  def __parseCACFG(self):
    """ Parse CA configuration file
    """
    block = ''
    self.cfg = {}
    self.supplied, self.optional, self.match, self.dnList = [], [], [], []
    with open(self.parameters['CAConfigFile'], "r") as caCFG:
      for line in caCFG:
        # Ignore comments
        line = re.sub(r'#.*', '', line)
        if re.findall(r"\[([A-Za-z0-9_]+)\]", line.replace(' ', '')):
          block = ''.join(re.findall(r"\[([A-Za-z0-9_]+)\]", line.replace(' ', '')))
          if block not in self.cfg:
            self.cfg[block] = {}
        if not block:
          continue
        if len(re.findall('=', line)) == 1:
          field, val = line.split('=')
          field = field.strip()
          variables = re.findall(r'[$]([A-Za-z0-9_]+)', val)
          for v in variables:
            for b in self.cfg:
              if v in self.cfg[b]:
                val = val.replace('$' + v, self.cfg[b][v])
          if 'default_ca' in self.cfg.get('ca', {}):
            if 'policy' in self.cfg.get(self.cfg['ca']['default_ca'], {}):
              if block == self.cfg[self.cfg['ca']['default_ca']]['policy']:
                self.dnList.append(field)
          self.cfg[block][field] = val.strip()

    self.bits = int(self.cfg['req'].get('default_bits') or self.bits)
    self.algoritm = self.cfg[self.cfg['ca']['default_ca']].get('default_md') or self.algoritm
    if not self.parameters.get('CertFile'):
      self.parameters['CertFile'] = self.cfg[self.cfg['ca']['default_ca']]['certificate']
      self.parameters['KeyFile'] = self.cfg[self.cfg['ca']['default_ca']]['private_key']
    # Read distinguished names
    for k, v in self.cfg[self.cfg[self.cfg['ca']['default_ca']]['policy']].items():
      nid = self.fields2nid[k]
      self.parameters[nid], self.minDict[nid], self.maxDict[nid] = [], [], []
      for k in ['0.' + k, k]:
        if k + '_default' in self.cfg['req']['distinguished_name']:
          self.parameters[nid].append(self.cfg['req']['distinguished_name'][k + '_default'])
        if k + '_min' in self.cfg['req']['distinguished_name']:
          self.minDict[nid].append(self.cfg['req']['distinguished_name'][k + '_min'])
        if k + '_max' in self.cfg['req']['distinguished_name']:
          self.maxDict[nid].append(self.cfg['req']['distinguished_name'][k + '_max'])
      if v == 'supplied':
        self.supplied.append(nid)
      elif v == 'optional':
        self.optional.append(nid)
      elif v == 'match':
        self.match.append(nid)

  def __parseDN(self, dn):
    """ Return DN fields

        :param str dn: DN

        :return: list -- contain tuple with positionOfField.fieldName, fieldNID, fieldValue
    """
    dnInfoDict = collections.OrderedDict()
    for f, v in [f.split('=') for f in dn.lstrip('/').split('/')]:
      if not v:
        return S_ERROR('No value set for "%s"' % f)
      if f not in dnInfoDict:
        dnInfoDict[f] = [v]
      else:
        dnInfoDict[f].append(v)
    return S_OK(dnInfoDict)

  def __fillX509Name(self, field, values):
    """ Fill x509_Name object by M2Crypto

        :param str field: DN field name
        :param list values: values of field, order important

        :return: S_OK()/S_ERROR()
    """
    for value in values:
      if value and m2.x509_name_set_by_nid(self.__X509Name.x509_name,  # pylint: disable=no-member
                                           self.fields2nid[field], value) == 0:
        if not self.__X509Name.add_entry_by_txt(field=field, type=ASN1.MBSTRING_ASC,
                                                entry=value, len=-1, loc=-1, set=0) == 1:
          return S_ERROR('Cannot set "%s" field.' % field)
    return S_OK()

  def __createCertM2Crypto(self):
    """ Create new certificate for user

        :return: S_OK(tuple)/S_ERROR() -- tuple contain certificate and pulic key as strings
    """
    # Create publik key
    userPubKey = EVP.PKey()
    userPubKey.assign_rsa(RSA.gen_key(self.bits, 65537, util.quiet_genparam_callback))
    # Create certificate
    userCert = X509.X509()
    userCert.set_pubkey(userPubKey)
    userCert.set_version(2)
    userCert.set_subject(self.__X509Name)
    userCert.set_serial_number(int(random.random() * 10 ** 10))
    # Add extentionals
    userCert.add_ext(X509.new_extension('basicConstraints', 'CA:' + str(False).upper()))
    userCert.add_ext(X509.new_extension('extendedKeyUsage', 'clientAuth', critical=1))
    # Set livetime
    validityTime = datetime.timedelta(days=400)
    notBefore = ASN1.ASN1_UTCTIME()
    notBefore.set_time(int(time.time()))
    notAfter = ASN1.ASN1_UTCTIME()
    notAfter.set_time(int(time.time()) + int(validityTime.total_seconds()))
    userCert.set_not_before(notBefore)
    userCert.set_not_after(notAfter)
    # Add subject from CA
    with open(self.parameters['CertFile']) as cf:
      caCertStr = cf.read()
    caCert = X509.load_cert_string(caCertStr)
    userCert.set_issuer(caCert.get_subject())
    # Use CA key
    with open(self.parameters['KeyFile']) as cf:
      caKeyStr = cf.read()
    pkey = EVP.PKey()
    pkey.assign_rsa(RSA.load_key_string(caKeyStr, callback=util.no_passphrase_callback))
    # Sign
    userCert.sign(pkey, self.algoritm)

    userCertStr = userCert.as_pem()
    userPubKeyStr = userPubKey.as_pem(cipher=None, callback=util.no_passphrase_callback)
    return S_OK((userCertStr, userPubKeyStr))

  def getFakeProxy(self, dn, time, group=None):
    """ Get fake proxy for tests

        :param str dn: fake DN
        :param int time: expired time in a seconds
        :param str group: if need to add fake DIRAC group

        :return: S_OK(dict)/S_ERROR() -- dict contain 'proxy' field with is a fake proxy string
    """
    self.__X509Name = X509.X509_Name()
    result = self.__parseDN(dn)
    if not result['OK']:
      return result
    dnInfoDict = result['Value']

    for field, values in dnInfoDict.items():
      result = self.__fillX509Name(field, values)
      if not result['OK']:
        return result

    result = self.__createCertM2Crypto()
    if result['OK']:
      certStr, keyStr = result['Value']
      chain = X509Chain()
      if chain.loadChainFromString(certStr)['OK'] and chain.loadKeyFromString(keyStr)['OK']:
        result = chain.generateProxyToString(time, rfc=True, diracGroup=group)
    if not result['OK']:
      return result
    chain = X509Chain()
    chain.loadProxyFromString(result['Value'])
    return S_OK((chain, result['Value']))
