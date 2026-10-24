package com.lab.demo

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Button(
                        onClick = { requestPermissionsAndStart() }
                    ) {
                        Text("Ativar serviço de localização (lab)")
                    }
                }
            }
        }
    }

    private fun requestPermissionsAndStart() {
        val perms = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.POST_NOTIFICATIONS
        )

        if (Build.VERSION.SDK_INT >= 34) {
            perms.add(
                Manifest.permission.FOREGROUND_SERVICE_LOCATION
            )
        }

        requestPermissions(
            perms.toTypedArray(),
            REQ_PERMS
        )
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(
            requestCode,
            permissions,
            grantResults
        )

        if (requestCode != REQ_PERMS) return

        if (!grantResults.all {
                it == PackageManager.PERMISSION_GRANTED
            }) {
            return
        }

        val bg = Manifest.permission.ACCESS_BACKGROUND_LOCATION

        if (checkSelfPermission(bg)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(
                arrayOf(bg),
                REQ_PERMS_BG
            )
            return
        }

        fetchTokenAndStart()
    }

    private fun fetchTokenAndStart() {
        Thread {
            val token = AuthClient.fetchToken(
                deviceId = "android-01"
            )

            if (token != null) {
                getSharedPreferences(
                    "lab",
                    MODE_PRIVATE
                )
                    .edit()
                    .putString("token", token)
                    .apply()

                startForegroundService(
                    Intent(
                        this,
                        LocationService::class.java
                    )
                )
            }
        }.start()
    }

    companion object {
        private const val REQ_PERMS = 1
        private const val REQ_PERMS_BG = 2
    }
}
