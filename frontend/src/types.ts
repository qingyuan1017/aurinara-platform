export interface User {
  id: string
  issuer: string
  subject: string
  email: string | null
  display_name: string | null
  status: 'active' | 'inactive'
  revision: number
}

export interface Study {
  id: string
  code: string
  display_name: string
  status: 'active' | 'archived'
  revision: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface Membership {
  id: string
  study_id: string
  user_id: string
  role_id: string
  role_name: string
  product: string
  site_id: string | null
  assigned_at: string
}

export interface Decision {
  allowed: boolean
  decision_id: string
  policy_version: number
  expires_at: string
  reason_code: string
}

export interface StoredObject {
  id: string
  study_id: string
  owner_product: string
  classification: string
  sha256: string | null
  size_bytes: number | null
  media_type: string
  status: string
  retention_until: string | null
  retention_state: string
  created_by: string
  created_at: string
  finalized_at: string | null
}

export interface ProblemPayload {
  error?: {
    code?: string
    message?: string
    details?: unknown
    request_id?: string
    correlation_id?: string
  }
}
