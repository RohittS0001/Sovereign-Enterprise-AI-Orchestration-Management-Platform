{{- define "sovereign.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sovereign.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "sovereign.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "sovereign.apiImage" -}}
{{ .Values.image.api.repository }}:{{ .Values.image.api.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "sovereign.webImage" -}}
{{ .Values.image.web.repository }}:{{ .Values.image.web.tag | default .Chart.AppVersion }}
{{- end -}}

{{/* Environment shared by the API pods and the migration job. */}}
{{- define "sovereign.apiEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "sovereign.fullname" . }}-config
  - secretRef:
      name: {{ .Values.existingSecret }}
{{- end -}}
