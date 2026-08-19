import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Data Processing Agreement — ProMarshal',
  description: 'Data Processing Agreement (DPA) between ProMarshal and its customers under GDPR Article 28.',
}

const LAST_UPDATED = 'March 25, 2026'
const CONTACT_EMAIL = 'legal@promarshal.ai'

export default function DPAPage() {
  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '80px 40px 120px' }}>

      {/* Header */}
      <div style={{ marginBottom: 64 }}>
        <div style={{
          display: 'inline-block',
          fontSize: 11, fontWeight: 700, letterSpacing: '2px',
          textTransform: 'uppercase', color: '#78a530',
          background: 'rgba(120,165,48,0.1)',
          border: '1px solid rgba(120,165,48,0.2)',
          borderRadius: 50, padding: '4px 14px', marginBottom: 20,
        }}>
          Legal
        </div>
        <h1 style={{
          fontSize: 'clamp(36px, 5vw, 56px)', fontWeight: 700,
          color: '#f0f4ff', letterSpacing: '-0.5px', lineHeight: 1.1,
          margin: '0 0 16px',
        }}>
          Data Processing Agreement
        </h1>
        <p style={{ color: '#94a3b8', fontSize: 16, margin: '0 0 8px' }}>
          Last updated: {LAST_UPDATED}
        </p>
        <p style={{ color: '#64748b', fontSize: 14, margin: 0 }}>
          This agreement is incorporated by reference into the ProMarshal Terms of Service.
        </p>
      </div>

      {/* Callout */}
      <div style={{
        background: 'rgba(120,165,48,0.06)',
        border: '1px solid rgba(120,165,48,0.2)',
        borderRadius: 12, padding: '20px 24px', marginBottom: 64,
      }}>
        <p style={{ color: '#94a3b8', fontSize: 14, lineHeight: 1.7, margin: 0 }}>
          This Data Processing Agreement (&quot;DPA&quot;) forms part of the agreement between ProMarshal (&quot;Processor&quot;) and the customer (&quot;Controller&quot;) and governs the processing of personal data by ProMarshal on behalf of the customer, as required under Article 28 of the General Data Protection Regulation (GDPR) and UK GDPR. By using ProMarshal, the customer agrees to this DPA. Enterprise customers may request a countersigned copy by contacting <a href={`mailto:${CONTACT_EMAIL}`} style={{ color: '#78a530' }}>{CONTACT_EMAIL}</a>.
        </p>
      </div>

      {/* Sections */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 56 }}>

        {/* 1. Definitions */}
        <div id="definitions">
          <h2 style={h2Style}>1. Definitions</h2>
          <div style={bodyStyle}>
            {`In this DPA, the following terms have the meanings set out below:

"Controller" means the customer entity that determines the purposes and means of processing Personal Data.

"Processor" means ProMarshal, which processes Personal Data on behalf of the Controller.

"Personal Data" means any information relating to an identified or identifiable natural person as defined under GDPR Article 4(1).

"Processing" means any operation performed on Personal Data, as defined under GDPR Article 4(2).

"Data Subject" means the natural person to whom Personal Data relates.

"GDPR" means Regulation (EU) 2016/679 of the European Parliament and of the Council.

"UK GDPR" means the GDPR as it forms part of United Kingdom domestic law by virtue of the European Union (Withdrawal) Act 2018.

"Standard Contractual Clauses" or "SCCs" means the standard contractual clauses adopted by the European Commission pursuant to Commission Decision 2021/914.

"Sub-processor" means any third party engaged by ProMarshal to process Personal Data on behalf of the Controller.

"Technical and Organisational Measures" or "TOMs" means the security measures described in Annex III of this DPA.`}
          </div>
        </div>

        {/* 2. Subject Matter */}
        <div id="subject-matter">
          <h2 style={h2Style}>2. Subject Matter and Duration</h2>
          <div style={bodyStyle}>
            {`ProMarshal processes Personal Data on behalf of the Controller solely to provide the ProMarshal platform and related services as described in the Terms of Service ("the Services").

The processing shall commence on the date the Controller begins using the Services and shall continue until termination of the Services or this DPA, whichever is earlier.

The nature, purpose, categories of Personal Data, and categories of Data Subjects are described in Annex I of this DPA.`}
          </div>
        </div>

        {/* 3. Processing Instructions */}
        <div id="instructions">
          <h2 style={h2Style}>3. Processing Instructions</h2>
          <div style={bodyStyle}>
            {`3.1 ProMarshal shall process Personal Data only on documented instructions from the Controller, including the instructions set out in this DPA and the Terms of Service, unless required to do so by applicable law.

3.2 If ProMarshal is required by applicable law to process Personal Data in a manner other than as instructed by the Controller, ProMarshal shall inform the Controller before such processing unless that law prohibits disclosure.

3.3 If ProMarshal reasonably believes that an instruction from the Controller infringes applicable data protection law, ProMarshal shall promptly notify the Controller. ProMarshal shall not be obliged to follow an instruction that it reasonably believes to be unlawful.

3.4 The Controller is responsible for ensuring that it has a lawful basis under applicable data protection law for all Personal Data it submits to the Services for processing.`}
          </div>
        </div>

        {/* 4. Confidentiality */}
        <div id="confidentiality">
          <h2 style={h2Style}>4. Confidentiality</h2>
          <div style={bodyStyle}>
            {`4.1 ProMarshal shall ensure that all personnel authorised to process Personal Data are subject to binding confidentiality obligations with respect to the Personal Data, whether by contract or applicable law.

4.2 ProMarshal shall limit access to Personal Data to only those personnel who require such access to perform the Services.

4.3 The confidentiality obligations survive the termination of this DPA and any employment or engagement.`}
          </div>
        </div>

        {/* 5. Security */}
        <div id="security">
          <h2 style={h2Style}>5. Security Measures</h2>
          <div style={bodyStyle}>
            {`5.1 ProMarshal shall implement and maintain appropriate technical and organisational measures to ensure a level of security appropriate to the risks presented by the processing, taking into account the state of the art, the costs of implementation, and the nature, scope, context, and purposes of processing, as well as the risk of varying likelihood and severity for the rights and freedoms of natural persons.

5.2 The Technical and Organisational Measures implemented by ProMarshal are described in Annex III of this DPA. ProMarshal may update these measures from time to time, provided that updates do not materially reduce the overall level of protection.

5.3 ProMarshal shall ensure that any Sub-processor engaged to process Personal Data implements equivalent security measures.`}
          </div>
        </div>

        {/* 6. Sub-processors */}
        <div id="sub-processors">
          <h2 style={h2Style}>6. Sub-processors</h2>
          <div style={bodyStyle}>
            {`6.1 The Controller grants ProMarshal general written authorisation to engage Sub-processors to process Personal Data in connection with the Services.

6.2 ProMarshal shall maintain an up-to-date list of Sub-processors (see Annex II). ProMarshal shall notify the Controller of any intended addition or replacement of Sub-processors by updating Annex II and notifying the Controller via email or in-app notice at least 14 days in advance.

6.3 The Controller may object to a new or replacement Sub-processor on reasonable data protection grounds within 14 days of notification. If the Controller objects and ProMarshal cannot reasonably accommodate the objection, the Controller may terminate the affected part of the Services without penalty.

6.4 ProMarshal shall ensure that each Sub-processor is bound by data protection obligations equivalent to those set out in this DPA, including sufficient guarantees to implement appropriate technical and organisational measures.

6.5 ProMarshal remains fully liable to the Controller for the acts and omissions of its Sub-processors.`}
          </div>
        </div>

        {/* 7. Data Subject Rights */}
        <div id="data-subject-rights">
          <h2 style={h2Style}>7. Data Subject Rights</h2>
          <div style={bodyStyle}>
            {`7.1 Taking into account the nature of the processing, ProMarshal shall assist the Controller by appropriate technical and organisational measures, insofar as possible, to fulfil the Controller's obligation to respond to requests from Data Subjects exercising their rights under applicable data protection law, including rights of:

• Access (Article 15 GDPR)
• Rectification (Article 16 GDPR)
• Erasure (Article 17 GDPR)
• Restriction of processing (Article 18 GDPR)
• Data portability (Article 20 GDPR)
• Objection (Article 21 GDPR)

7.2 If ProMarshal receives a request directly from a Data Subject, it shall promptly inform the Controller and shall not respond to the Data Subject directly unless instructed by the Controller or required by applicable law.

7.3 ProMarshal shall not charge the Controller for reasonable assistance in responding to Data Subject requests unless the volume of requests is exceptional and agreed in advance.`}
          </div>
        </div>

        {/* 8. Compliance Assistance */}
        <div id="compliance">
          <h2 style={h2Style}>8. Compliance Assistance</h2>
          <div style={bodyStyle}>
            {`ProMarshal shall assist the Controller in ensuring compliance with its obligations under Articles 32–36 of GDPR, taking into account the nature of processing and the information available to ProMarshal, including with respect to:

• Security of processing (Article 32)
• Notification of Personal Data breaches to supervisory authorities (Article 33)
• Communication of Personal Data breaches to Data Subjects (Article 34)
• Data Protection Impact Assessments (Article 35)
• Prior consultation with supervisory authorities (Article 36)

ProMarshal shall promptly provide the Controller with all information reasonably necessary to demonstrate compliance with this DPA upon written request.`}
          </div>
        </div>

        {/* 9. Data Breach Notification */}
        <div id="breach">
          <h2 style={h2Style}>9. Personal Data Breach Notification</h2>
          <div style={bodyStyle}>
            {`9.1 ProMarshal shall notify the Controller without undue delay, and in any event within 48 hours, after becoming aware of a Personal Data breach affecting Personal Data processed on behalf of the Controller.

9.2 The notification shall include, to the extent available at the time:

• A description of the nature of the breach, including the categories and approximate number of Data Subjects and Personal Data records affected
• The name and contact details of the Data Protection contact at ProMarshal (${CONTACT_EMAIL})
• A description of the likely consequences of the breach
• A description of the measures taken or proposed to address the breach

9.3 Where the required information cannot be provided at the same time, ProMarshal may provide it in phases, without undue further delay.

9.4 ProMarshal shall cooperate with the Controller and provide all reasonable assistance in preparing notifications to supervisory authorities and, where applicable, to affected Data Subjects.

9.5 A notification under this clause shall not constitute an admission of fault or liability by ProMarshal.`}
          </div>
        </div>

        {/* 10. International Transfers */}
        <div id="transfers">
          <h2 style={h2Style}>10. International Data Transfers</h2>
          <div style={bodyStyle}>
            {`10.1 ProMarshal shall not transfer Personal Data originating from the EEA or UK to a country outside the EEA or UK that does not benefit from an adequacy decision, without ensuring an appropriate safeguard is in place in accordance with GDPR Chapter V.

10.2 Where ProMarshal transfers Personal Data to Sub-processors located outside the EEA or UK, it shall ensure that such transfers are governed by the Standard Contractual Clauses (Module 3: Processor to Sub-processor) or another appropriate transfer mechanism under applicable law.

10.3 To the extent that the Controller transfers Personal Data to ProMarshal and ProMarshal is located outside the EEA or UK, the parties agree that the Standard Contractual Clauses (Module 2: Controller to Processor) are incorporated into and form part of this DPA.

10.4 The processing locations of Personal Data are specified in Annex II.`}
          </div>
        </div>

        {/* 11. Audit Rights */}
        <div id="audit">
          <h2 style={h2Style}>11. Audit Rights</h2>
          <div style={bodyStyle}>
            {`11.1 ProMarshal shall make available to the Controller all information necessary to demonstrate compliance with this DPA and shall allow for and contribute to audits, including inspections, conducted by the Controller or a third-party auditor mandated by the Controller.

11.2 Audits shall be:
• Conducted no more than once per 12-month period, unless the Controller has reasonable cause to believe a breach has occurred
• Subject to at least 30 days' prior written notice (except in the case of a confirmed data breach)
• Conducted during normal business hours and in a manner that minimises disruption to ProMarshal's operations
• At the Controller's cost unless the audit reveals a material breach of this DPA

11.3 As an alternative to on-site audits, ProMarshal may satisfy audit obligations by providing the Controller with relevant third-party audit reports (such as SOC 2 Type II or ISO 27001 certification) upon written request.

11.4 The Controller and any third-party auditor shall sign appropriate confidentiality undertakings before receiving any audit information. ProMarshal may redact commercially sensitive information not relevant to the audit scope.`}
          </div>
        </div>

        {/* 12. Deletion and Return */}
        <div id="deletion">
          <h2 style={h2Style}>12. Deletion and Return of Personal Data</h2>
          <div style={bodyStyle}>
            {`12.1 Upon termination or expiry of the Services, ProMarshal shall, at the Controller's written direction:

(a) Delete all Personal Data and existing copies thereof (including copies held by Sub-processors), unless applicable law requires continued retention; or
(b) Return all Personal Data to the Controller in a structured, commonly used, machine-readable format (CSV or JSON).

12.2 ProMarshal shall complete deletion or return within 30 days of the Controller's written request or termination of the Services, whichever is earlier.

12.3 ProMarshal shall provide the Controller with written certification of deletion upon request.

12.4 Notwithstanding the above, ProMarshal may retain one encrypted copy of Personal Data for a period not exceeding 90 days solely for legal, compliance, or dispute resolution purposes, provided that ProMarshal continues to comply with its confidentiality and security obligations in respect of any retained data.

12.5 Deletion includes data held in backups, which shall be deleted as backup retention cycles permit, but no later than 90 days following the deletion request.`}
          </div>
        </div>

        {/* 13. Liability */}
        <div id="liability">
          <h2 style={h2Style}>13. Liability</h2>
          <div style={bodyStyle}>
            {`13.1 Each party's liability under this DPA shall be subject to the limitations and exclusions of liability set out in the ProMarshal Terms of Service.

13.2 ProMarshal shall be liable to the Controller for damages arising from ProMarshal's breach of this DPA, to the extent that such damages are directly caused by such breach.

13.3 ProMarshal shall not be liable for any processing carried out in accordance with the Controller's instructions where those instructions are unlawful or infringe applicable data protection law.

13.4 Where both the Controller and ProMarshal are responsible for damage caused by processing, liability shall be apportioned in proportion to each party's respective fault and responsibility.

13.5 Nothing in this DPA limits either party's liability for death or personal injury caused by negligence, fraud, or any other liability that cannot be limited by law.`}
          </div>
        </div>

        {/* 14. Term and Governing Law */}
        <div id="term">
          <h2 style={h2Style}>14. Term, Termination and Governing Law</h2>
          <div style={bodyStyle}>
            {`14.1 This DPA shall remain in force for the duration of the Services and shall terminate automatically upon termination of the Services, subject to survival of obligations relating to data deletion, confidentiality, and audit.

14.2 Obligations that by their nature should survive termination (including Sections 4, 9, 11, and 12) shall survive termination of this DPA.

14.3 This DPA is governed by the same governing law as the Terms of Service, save that where the Controller is established in the EU or UK, the mandatory provisions of applicable data protection law shall apply regardless of the governing law.

14.4 Any disputes arising under this DPA shall be resolved in accordance with the dispute resolution provisions in the Terms of Service.`}
          </div>
        </div>

        {/* Annex I */}
        <div id="annex-i">
          <h2 style={{ ...h2Style, color: '#78a530' }}>Annex I — Processing Details</h2>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
            <div>
              <h3 style={h3Style}>A. Parties</h3>
              <div style={bodyStyle}>
                {`Data Controller: The customer entity executing the ProMarshal Terms of Service.
Data Processor: ProMarshal (the company operating the ProMarshal platform).`}
              </div>
            </div>

            <div>
              <h3 style={h3Style}>B. Description of Processing</h3>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Item</th>
                    <th style={thStyle}>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['Purpose of processing', 'Delivering automated project coordination services including task reminders, Jira sync, Slack messaging, team polling, and AI-powered project insights'],
                    ['Nature of processing', 'Collection, storage, retrieval, structuring, use, disclosure to sub-processors, deletion'],
                    ['Duration', 'For the term of the Services, plus up to 90 days post-termination for deletion'],
                  ].map(([item, detail], i) => (
                    <tr key={i}>
                      <td style={tdStyle}>{item}</td>
                      <td style={tdStyle}>{detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <h3 style={h3Style}>C. Categories of Personal Data</h3>
              <div style={bodyStyle}>
                {`• Name and email address of team members
• Slack user IDs, workspace IDs, and channel information
• Jira account IDs, project names, issue titles, descriptions, statuses, assignees, due dates, and comments
• Task update responses and status messages provided via Slack
• Poll responses and preferences
• Login session data and authentication tokens`}
              </div>
            </div>

            <div>
              <h3 style={h3Style}>D. Categories of Data Subjects</h3>
              <div style={bodyStyle}>
                {`• Employees, contractors, and team members of the Controller
• Project managers and team leads using the ProMarshal platform
• Any other individuals whose personal data is contained within the Controller's Jira or Slack workspace`}
              </div>
            </div>
          </div>
        </div>

        {/* Annex II */}
        <div id="annex-ii">
          <h2 style={{ ...h2Style, color: '#78a530' }}>Annex II — Approved Sub-processors</h2>
          <p style={{ color: '#94a3b8', fontSize: 14, marginBottom: 24 }}>
            The following sub-processors are approved as of the last updated date. ProMarshal will notify the Controller of changes per Section 6.2.
          </p>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Sub-processor</th>
                <th style={thStyle}>Purpose</th>
                <th style={thStyle}>Processing Location</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['MongoDB Atlas (MongoDB, Inc.)', 'Database storage for all user, project, and task data', 'USA (AWS us-east-1)'],
                ['Slack Technologies, LLC', 'Delivery of task reminders and interaction handling via Slack', 'USA'],
                ['Atlassian (Jira)', 'Task synchronisation and status updates', 'USA / Australia'],
                ['OpenAI, L.L.C.', 'AI-generated message content for paid tier users', 'USA'],
                ['Anthropic, PBC', 'AI-generated message content (alternative provider)', 'USA'],
                ['SendGrid (Twilio Inc.)', 'Transactional email delivery (OTP, notifications)', 'USA'],
                ['Amazon Web Services (AWS)', 'Backend and frontend application hosting', 'USA'],
                ['Google LLC', 'OAuth sign-in authentication', 'USA'],
                ['Redis (Redis Ltd.)', 'Session state and background job queuing', 'USA'],
              ].map(([name, purpose, location], i) => (
                <tr key={i}>
                  <td style={tdStyle}>{name}</td>
                  <td style={tdStyle}>{purpose}</td>
                  <td style={tdStyle}>{location}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Annex III */}
        <div id="annex-iii">
          <h2 style={{ ...h2Style, color: '#78a530' }}>Annex III — Technical and Organisational Measures</h2>
          <p style={{ color: '#94a3b8', fontSize: 14, marginBottom: 32 }}>
            ProMarshal implements the following measures to ensure appropriate security of Personal Data:
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
            {[
              {
                title: 'Encryption',
                items: [
                  'All data in transit encrypted using TLS 1.2 or higher',
                  'OAuth tokens and credentials encrypted at rest using AES-256',
                  'Database storage encrypted at rest by MongoDB Atlas',
                ],
              },
              {
                title: 'Access Controls',
                items: [
                  'Role-based access control (RBAC) for production systems',
                  'Multi-factor authentication required for all internal systems',
                  'Principle of least privilege applied to all personnel and service accounts',
                  'Access logs maintained and reviewed regularly',
                ],
              },
              {
                title: 'System Security',
                items: [
                  'Regular security patching and dependency updates',
                  'Automated vulnerability scanning on code repositories',
                  'Application firewall and DDoS protection via hosting provider',
                  'Secrets managed via environment variables, not hardcoded in source code',
                ],
              },
              {
                title: 'Organisational Measures',
                items: [
                  'All personnel with data access bound by confidentiality obligations',
                  'Internal security policies covering acceptable use, incident response, and data handling',
                  'Data breach response plan with defined roles and timelines',
                  'Sub-processor security requirements contractually enforced',
                ],
              },
              {
                title: 'Availability and Resilience',
                items: [
                  'Automated database backups with point-in-time recovery',
                  'Application hosted on infrastructure with high availability guarantees',
                  'Monitoring and alerting for system health and anomalies',
                  'Disaster recovery procedures documented and tested',
                ],
              },
              {
                title: 'Data Minimisation',
                items: [
                  'Only Personal Data necessary to provide the Services is collected',
                  'Data retention periods defined and enforced',
                  'Automated deletion of expired session data and tokens',
                ],
              },
            ].map(section => (
              <div key={section.title} style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 12, padding: '20px 24px',
              }}>
                <h3 style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0', margin: '0 0 14px' }}>
                  {section.title}
                </h3>
                <ul style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {section.items.map((item, i) => (
                    <li key={i} style={{ color: '#94a3b8', fontSize: 14, lineHeight: 1.6 }}>{item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

      </div>

      {/* Contact CTA */}
      <div style={{
        marginTop: 80,
        background: 'linear-gradient(135deg, rgba(120,165,48,0.08), rgba(120,165,48,0.04))',
        border: '1px solid rgba(120,165,48,0.2)',
        borderRadius: 20, padding: '40px',
        textAlign: 'center',
      }}>
        <h3 style={{ fontSize: 20, fontWeight: 700, color: '#f0f4ff', margin: '0 0 12px' }}>
          Need a countersigned DPA?
        </h3>
        <p style={{ color: '#94a3b8', fontSize: 15, margin: '0 0 24px' }}>
          Enterprise customers and EU businesses can request a countersigned copy of this agreement.
        </p>
        <a href={`mailto:${CONTACT_EMAIL}`} style={{
          display: 'inline-block',
          background: '#78a530', color: '#fff',
          padding: '12px 28px', borderRadius: 50,
          fontSize: 15, fontWeight: 500, textDecoration: 'none',
        }}>
          Request signed DPA — {CONTACT_EMAIL}
        </a>
      </div>

    </div>
  )
}

const h2Style: React.CSSProperties = {
  fontSize: 22, fontWeight: 700, color: '#f0f4ff',
  marginBottom: 20, paddingBottom: 12,
  borderBottom: '1px solid rgba(255,255,255,0.08)',
}

const h3Style: React.CSSProperties = {
  fontSize: 16, fontWeight: 600, color: '#e2e8f0', marginBottom: 10,
}

const bodyStyle: React.CSSProperties = {
  color: '#94a3b8', fontSize: 15, lineHeight: 1.8, whiteSpace: 'pre-line',
}

const tableStyle: React.CSSProperties = {
  width: '100%', borderCollapse: 'collapse',
  border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12,
  overflow: 'hidden', fontSize: 14,
}

const thStyle: React.CSSProperties = {
  padding: '12px 16px', textAlign: 'left',
  background: 'rgba(255,255,255,0.05)',
  color: '#e2e8f0', fontWeight: 600, fontSize: 13,
  borderBottom: '1px solid rgba(255,255,255,0.08)',
}

const tdStyle: React.CSSProperties = {
  padding: '12px 16px', color: '#94a3b8',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
  verticalAlign: 'top', lineHeight: 1.6,
}
