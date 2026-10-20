<?php
/**
 * Plugin Name:       InvenTree Shop
 * Plugin URI:        https://github.com/SXSLYDA/inventree-woocommerce
 * Description:       Companion to the inventree-woocommerce InvenTree plugin. Quote requests with drawings, samples or a call (itemised, accepted online), and a "Charge to my account" payment method for customers InvenTree has approved for credit.
 * Version:           0.6.0
 * Requires at least: 6.4
 * Requires PHP:      7.4
 * Requires Plugins:  woocommerce
 * Author:            Tyler Brannigan / OMG Harness
 * License:           MIT
 * Text Domain:       inventree-shop
 *
 * WC requires at least: 8.0
 */

defined( 'ABSPATH' ) || exit;

define( 'INVENTREE_SHOP_VERSION', '0.6.0' );
define( 'INVENTREE_SHOP_FILE', __FILE__ );

// User meta written by the InvenTree plugin through the WooCommerce REST API.
define( 'INVENTREE_META_APPROVED', 'inventree_account_approved' );
define( 'INVENTREE_META_CREDIT', 'inventree_credit_available' );
define( 'INVENTREE_META_COMPANY', 'inventree_account_company' );
// Order meta read by the InvenTree plugin (no leading underscore, so it is included in webhooks).
define( 'INVENTREE_META_PO', 'inventree_po_number' );

require_once __DIR__ . '/includes/class-inventree-link.php';
require_once __DIR__ . '/includes/class-inventree-settings.php';

add_action(
	'plugins_loaded',
	function () {
		if ( ! class_exists( 'WooCommerce' ) ) {
			return;
		}
		require_once __DIR__ . '/includes/class-inventree-quotes.php';
		require_once __DIR__ . '/includes/class-inventree-notify.php';
		InvenTree_Shop_Settings::init();
		InvenTree_Shop_Quotes::init();
		InvenTree_Shop_Notify::init();
	},
	20
);

register_activation_hook(
	__FILE__,
	function () {
		add_rewrite_endpoint( 'quotes', EP_ROOT | EP_PAGES );
		flush_rewrite_rules();
	}
);

register_deactivation_hook(
	__FILE__,
	function () {
		wp_clear_scheduled_hook( 'inventree_shop_retry_quotes' );
		flush_rewrite_rules();
	}
);

// Works with WooCommerce's High-Performance Order Storage and the block checkout.
add_action(
	'before_woocommerce_init',
	function () {
		if ( class_exists( \Automattic\WooCommerce\Utilities\FeaturesUtil::class ) ) {
			\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility( 'custom_order_tables', INVENTREE_SHOP_FILE, true );
			\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility( 'cart_checkout_blocks', INVENTREE_SHOP_FILE, true );
		}
	}
);

/**
 * The current user's account standing, as InvenTree last reported it.
 *
 * @param int|null $user_id User id (default: current user).
 * @return array{approved: bool, credit: float|null, company: string}
 */
function inventree_shop_account( $user_id = null ) {
	$user_id = $user_id ? (int) $user_id : get_current_user_id();
	if ( ! $user_id ) {
		return array( 'approved' => false, 'credit' => null, 'company' => '' );
	}
	$credit = get_user_meta( $user_id, INVENTREE_META_CREDIT, true );
	return array(
		'approved' => 'yes' === get_user_meta( $user_id, INVENTREE_META_APPROVED, true ),
		'credit'   => ( '' === $credit || null === $credit ) ? null : (float) $credit, // null = no limit
		'company'  => (string) get_user_meta( $user_id, INVENTREE_META_COMPANY, true ),
	);
}

/**
 * Can this user charge $amount to their account?
 *
 * @param float    $amount  Order total.
 * @param int|null $user_id User id (default: current user).
 */
function inventree_shop_can_charge( $amount, $user_id = null ) {
	$account = inventree_shop_account( $user_id );
	if ( ! $account['approved'] ) {
		return false;
	}
	return null === $account['credit'] || (float) $amount <= $account['credit'] + 0.005;
}

add_action(
	'plugins_loaded',
	function () {
		if ( ! class_exists( 'WC_Payment_Gateway' ) ) {
			return;
		}
		require_once __DIR__ . '/includes/class-inventree-account-gateway.php';

		add_filter(
			'woocommerce_payment_gateways',
			function ( $gateways ) {
				$gateways[] = 'InvenTree_Account_Gateway';
				return $gateways;
			}
		);
	}
);

add_action(
	'woocommerce_blocks_loaded',
	function () {
		if ( ! class_exists( \Automattic\WooCommerce\Blocks\Payments\Integrations\AbstractPaymentMethodType::class ) ) {
			return;
		}
		require_once __DIR__ . '/includes/class-inventree-account-blocks.php';
		add_action(
			'woocommerce_blocks_payment_method_type_registration',
			function ( $registry ) {
				$registry->register( new InvenTree_Account_Blocks() );
			}
		);
	}
);

// Show the customer's PO number on the admin order screen and in order emails.
add_action(
	'woocommerce_admin_order_data_after_billing_address',
	function ( $order ) {
		$po = $order->get_meta( INVENTREE_META_PO );
		if ( $po ) {
			echo '<p><strong>' . esc_html__( 'Customer PO:', 'inventree-shop' ) . '</strong> ' . esc_html( $po ) . '</p>';
		}
	}
);

add_filter(
	'woocommerce_email_order_meta_fields',
	function ( $fields, $sent_to_admin, $order ) {
		$po = $order->get_meta( INVENTREE_META_PO );
		if ( $po ) {
			$fields['inventree_po'] = array(
				'label' => __( 'Purchase order', 'inventree-shop' ),
				'value' => $po,
			);
		}
		return $fields;
	},
	10,
	3
);

// Account standing on the customer's My Account dashboard.
add_action(
	'woocommerce_account_dashboard',
	function () {
		$account = inventree_shop_account();
		if ( ! $account['approved'] ) {
			return;
		}
		echo '<div class="inventree-account-summary woocommerce-info">';
		/* translators: %s: company name */
		echo esc_html( sprintf( __( 'Trade account: %s.', 'inventree-shop' ), $account['company'] ) ) . ' ';
		if ( null === $account['credit'] ) {
			esc_html_e( 'You can charge orders to your account at checkout.', 'inventree-shop' );
		} else {
			/* translators: %s: available credit */
			echo wp_kses_post( sprintf( __( 'Available credit: %s.', 'inventree-shop' ), wc_price( $account['credit'] ) ) );
		}
		echo '</div>';
	}
);
